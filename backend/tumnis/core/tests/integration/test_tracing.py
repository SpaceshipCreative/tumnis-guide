"""One trace from the request through the outbox into every subscriber (P0-27, REL-5).

`POST /v1/projects` and its subscribers arrive with P0-17 and later; until then a route
added by the test emits `test.ping`, whose subscribers are `_deliveries`' `testa.record`
and `testb.record`. The chain under test (request span -> carrier on the outbox row ->
subscriber spans) is the same.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from uuid import UUID

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    import httpx
    from dbos import DBOS
    from fastapi import FastAPI
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

EMIT_PATH = "/v1/test-emit/{note}"
PING_SUBSCRIBERS = ("testa.record", "testb.record")


def _add_emit_route(app: FastAPI, workspace: WorkspaceHandle, clock: FixedClock) -> None:
    """POST /v1/test-emit/{note}: emits test.ping in the workspace, in the request. Put
    first so the shell mount at "/" (when the frontend is built) does not shadow it."""
    from fastapi.routing import APIRoute  # noqa: PLC0415

    from tumnis.core.events import TestPingV1  # noqa: PLC0415
    from tumnis.core.outbox import emit  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    async def emit_ping(note: str) -> dict[str, str]:
        async with tenant_session(workspace.ctx) as session:
            event_id = await emit(session, TestPingV1(note=note), occurred_at=clock.now())
        return {"event_id": str(event_id)}

    app.router.routes.insert(0, APIRoute(EMIT_PATH, emit_ping, methods=["POST"]))


def _server_span(exporter: InMemorySpanExporter) -> ReadableSpan:
    from opentelemetry.trace import SpanKind  # noqa: PLC0415

    spans = [s for s in exporter.get_finished_spans() if s.kind == SpanKind.SERVER]
    assert len(spans) == 1, [s.name for s in spans]
    return spans[0]


def _trace_context(db: DbUrls, event_id: UUID) -> dict[str, Any]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        row = conn.execute(
            "SELECT trace_context FROM outbox WHERE event_id = %s", (event_id,)
        ).fetchone()
    assert row is not None
    return dict(row[0])


@pytest.mark.req("REL-5")
@pytest.mark.wp("P0-27")
@pytest.mark.xfail(strict=True, reason="spec:P0-27")
async def test_request_trace_id_is_stored_on_outbox_row(
    app: FastAPI,
    client: httpx.AsyncClient,
    *,
    span_exporter: InMemorySpanExporter,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P0-27-01
    A request that emits an event stores the W3C carrier on the outbox row: the trace ID in
    `trace_context["traceparent"]` is the server span's, and its parent ID is that span's.
    """
    _add_emit_route(app, workspace, clock)
    response = await client.post("/v1/test-emit/traced")
    assert response.status_code == 200, response.text

    server = _server_span(span_exporter)
    carrier = _trace_context(db, UUID(response.json()["event_id"]))
    version, trace_id, span_id, _flags = carrier["traceparent"].split("-")
    assert version == "00"
    assert trace_id == format(server.context.trace_id, "032x")
    assert span_id == format(server.context.span_id, "016x")


@pytest.mark.req("REL-5")
@pytest.mark.wp("P0-27")
@pytest.mark.xfail(strict=True, reason="spec:P0-27")
async def test_subscriber_spans_continue_the_request_trace(
    app: FastAPI,
    client: httpx.AsyncClient,
    *,
    span_exporter: InMemorySpanExporter,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    dbos: type[DBOS],
) -> None:
    """T-P0-27-02
    After the relay delivers the event, the exporter holds `event test.ping -> testa.record`
    and `event test.ping -> testb.record`, each in the request's trace with the request span
    as its parent and the event ID as an attribute.
    """
    from tumnis.core.events import relay_once  # noqa: PLC0415
    from tumnis.core.tests.integration import _deliveries  # noqa: PLC0415

    _deliveries.create_table(db.libpq(OWNER))
    _add_emit_route(app, workspace, clock)
    response = await client.post("/v1/test-emit/relayed")
    assert response.status_code == 200, response.text
    event_id = response.json()["event_id"]
    server = _server_span(span_exporter)

    assert await relay_once() == 1
    wanted = {f"event test.ping -> {sub}" for sub in PING_SUBSCRIBERS}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 20
    while True:
        spans = {s.name: s for s in span_exporter.get_finished_spans() if s.name in wanted}
        if set(spans) == wanted:
            break
        if loop.time() > deadline:
            pytest.fail(f"subscriber spans {sorted(spans)} of {sorted(wanted)} after 20 s")
        await asyncio.sleep(0.05)

    for span in spans.values():
        assert span.context.trace_id == server.context.trace_id
        assert span.parent is not None
        assert span.parent.span_id == server.context.span_id
        assert span.attributes is not None
        assert span.attributes["tumnis.event_id"] == event_id
