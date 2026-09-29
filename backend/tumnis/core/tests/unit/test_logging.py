"""JSON logs with trace IDs, and redaction as the last step before rendering (P0-27, REL-5,
SEC-6)."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from tests.fixtures import JsonLogs

HEX32 = re.compile(r"^[0-9a-f]{32}$")


@pytest.mark.req("REL-5")
@pytest.mark.wp("P0-27")
async def test_every_line_is_json_with_trace_id(
    capture_json_logs: JsonLogs, span_exporter: InMemorySpanExporter
) -> None:
    """T-P0-27-06
    During one request an app logger (structlog), `uvicorn.error` and the `dbos` logger each
    write a line; one more `uvicorn.error` line follows outside the request. Every line on
    the handler parses as JSON, and the three lines inside the request carry the request's
    32-hex `trace_id`.
    """
    import httpx  # noqa: PLC0415
    import structlog  # noqa: PLC0415
    from fastapi import FastAPI  # noqa: PLC0415
    from opentelemetry.trace import SpanKind  # noqa: PLC0415

    from tumnis.core import telemetry  # noqa: PLC0415

    app = FastAPI()

    @app.get("/v1/things/{thing_id}")
    async def get_thing(thing_id: str) -> dict[str, str]:
        structlog.get_logger("tumnis.test").info("app line", thing_id=thing_id)
        logging.getLogger("uvicorn.error").info("uvicorn line")
        logging.getLogger("dbos").info("dbos line")
        return {"id": thing_id}

    telemetry.instrument_app(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as http:
        assert (await http.get("/v1/things/42")).status_code == 200
    logging.getLogger("uvicorn.error").info("outside line")

    lines = capture_json_logs.lines()  # raises on any line that is not JSON
    by_event = {line["event"]: line for line in lines}
    assert {"app line", "uvicorn line", "dbos line", "outside line"} <= set(by_event)
    server = [s for s in span_exporter.get_finished_spans() if s.kind == SpanKind.SERVER]
    assert len(server) == 1
    request_trace = format(server[0].context.trace_id, "032x")
    for event in ("app line", "uvicorn line", "dbos line"):
        assert HEX32.match(by_event[event]["trace_id"]), by_event[event]
        assert by_event[event]["trace_id"] == request_trace
    assert "trace_id" not in by_event["outside line"]
    assert by_event["app line"]["thing_id"] == "42"
    assert by_event["app line"]["level"] == "info"


@pytest.mark.req("REL-5", "SEC-6")
@pytest.mark.wp("P0-27")
def test_redaction_still_applies_after_trace_ids(
    capture_json_logs: JsonLogs, span_exporter: InMemorySpanExporter
) -> None:
    """T-P0-27-07
    The shared chain adds trace IDs before it redacts, and redaction is its last step; a
    structlog line and a stdlib line written inside a span keep their trace IDs while the
    secrets, bodies and bearer tokens in them never reach the rendered output.
    """
    import structlog  # noqa: PLC0415

    from tumnis.core import telemetry  # noqa: PLC0415
    from tumnis.core.logging import SHARED, add_trace_ids, redact  # noqa: PLC0415

    assert SHARED[-1] is redact
    assert SHARED.index(add_trace_ids) < SHARED.index(redact)

    with telemetry.tracer().start_as_current_span("unit"):
        structlog.get_logger("tumnis.test").info(
            "signed in with Bearer abc.def-ghi",
            token="s3cret-token-value",
            body="the whole message body",
            nested={"password": "hunter2-pass", "fine": "kept-value"},
        )
        logging.getLogger("uvicorn.error").warning(
            "retry with password=pw-in-text",
            extra={"authorization": "Bearer zzz-header", "prompt": "a long prompt"},
        )

    text = capture_json_logs.text()
    for secret in (
        "abc.def-ghi",
        "s3cret-token-value",
        "the whole message body",
        "hunter2-pass",
        "pw-in-text",
        "zzz-header",
        "a long prompt",
    ):
        assert secret not in text
    lines = capture_json_logs.lines()
    assert len(lines) == 2
    assert all(HEX32.match(line["trace_id"]) for line in lines)
    assert "kept-value" in text
