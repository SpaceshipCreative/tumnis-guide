"""Usage counters rise once per event: the ledger guards against double delivery, the
unique keys carry the race, counters are per workspace and per UTC day (P0-21)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.usage.tests.integration import _usage

if TYPE_CHECKING:
    import httpx
    from dbos import DBOS, WorkflowHandleAsync
    from fastapi import FastAPI

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

# The usage subscriber of `task.created`. Its name is part of every delivery's workflow ID,
# so it is spelled out here: renaming it would count old events again.
TASKS_SUBSCRIBER = "usage.count_task_created"
TODAY = date(2026, 3, 9)  # the clock fixture's day


def _task_payload() -> dict[str, Any]:
    return {
        "task_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "label": None,
        "source": "user",
        "tainted": False,
        "schema_version": 1,
    }


@pytest.mark.req("Hosted readiness")
@pytest.mark.wp("P0-21")
async def test_counter_rises_once_when_event_delivered_twice(
    db: DbUrls, dbos: type[DBOS], workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P0-21-03
    Create a task (one `task.created`); the relay delivers it to the usage subscriber. Then
    the same envelope is enqueued to that subscriber again under a new workflow ID (a
    dead-letter retry after a success): both deliveries succeed and `tasks_created` for
    today is 1.
    """
    from dbos import SetWorkflowID  # noqa: PLC0415

    import tumnis.modules.usage.events  # noqa: F401, PLC0415  # registers the subscribers
    from tumnis.core.events import (  # noqa: PLC0415
        EVENTS_QUEUE,
        deliver_event,
        delivery_id,
        relay_once,
        subscribers_for,
    )

    assert TASKS_SUBSCRIBER in {sub.name for sub in subscribers_for("task.created")}

    event_id = await _usage.create_task(workspace.ctx, clock.now())
    assert await relay_once() == 1
    first: WorkflowHandleAsync[str] = await dbos.retrieve_workflow_async(
        delivery_id(event_id, TASKS_SUBSCRIBER)
    )
    assert await first.get_result() == "delivered"
    assert _usage.counter_value(db, workspace.id, TODAY, "tasks_created") == 1

    envelope = _usage.outbox_envelope(db, event_id)
    with SetWorkflowID(f"{delivery_id(event_id, TASKS_SUBSCRIBER)}:retry-1"):
        again: WorkflowHandleAsync[str] = await dbos.enqueue_workflow_async(
            EVENTS_QUEUE, deliver_event, TASKS_SUBSCRIBER, envelope.model_dump(mode="json")
        )
    assert await again.get_result() == "delivered"

    assert _usage.counter_value(db, workspace.id, TODAY, "tasks_created") == 1
    assert _usage.ledger_rows(db, workspace.id, event_id) == 1


@pytest.mark.req("Hosted readiness")
@pytest.mark.wp("P0-21")
async def test_concurrent_duplicate_deliveries_count_once(
    db: DbUrls, workspace: WorkspaceHandle
) -> None:
    """T-P0-21-04
    For each of five envelopes, two sessions call `usage.api.record(env)` at once through
    `asyncio.gather`: one records 1 row, the other 0; `tasks_created` is 5 and each event
    has one ledger row.
    """
    from tests.fixtures import make_envelope  # noqa: PLC0415
    from tumnis.core.events import EventEnvelope  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.usage.api import record  # noqa: PLC0415

    async def deliver(env: EventEnvelope) -> int:
        async with tenant_session(workspace.ctx) as session:
            return await record(session, env)

    envelopes = [make_envelope("task.created", _task_payload(), workspace) for _ in range(5)]
    async with _usage.configured(db):
        for env in envelopes:
            results = await asyncio.gather(deliver(env), deliver(env))
            assert sorted(results) == [0, 1]

    assert _usage.counter_value(db, workspace.id, TODAY, "tasks_created") == 5
    for env in envelopes:
        assert _usage.ledger_rows(db, workspace.id, env.event_id) == 1


@pytest.mark.req("Hosted readiness")
@pytest.mark.wp("P0-21")
async def test_counters_are_per_workspace(
    db: DbUrls, two_workspaces: tuple[WorkspaceHandle, WorkspaceHandle]
) -> None:
    """T-P0-21-05
    Two `task.created` in A, one in B: A's report says 2 and B's says 1; recording A's
    envelope inside B's context is refused, not counted for B.
    """
    from sqlalchemy.exc import DBAPIError  # noqa: PLC0415

    from tests.fixtures import make_envelope  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.modules.usage.api import record, report  # noqa: PLC0415

    a, b = two_workspaces

    async def tasks_created(ctx: WorkspaceContext) -> list[tuple[date, int]]:
        async with tenant_session(ctx) as session:
            rows = await report(session, TODAY, TODAY)
        return [(row.day, row.value) for row in rows if row.counter == "tasks_created"]

    async with _usage.configured(db):
        for handle, n in ((a, 2), (b, 1)):
            for _ in range(n):
                async with tenant_session(handle.ctx) as session:
                    assert await record(session, make_envelope("task.created", {}, handle)) == 1

        assert await tasks_created(a.ctx) == [(TODAY, 2)]
        assert await tasks_created(b.ctx) == [(TODAY, 1)]

        with pytest.raises(DBAPIError):
            async with tenant_session(b.ctx) as session:
                await record(session, make_envelope("task.created", {}, a))
        assert await tasks_created(b.ctx) == [(TODAY, 1)]

    assert _usage.counter_value(db, a.id, TODAY, "tasks_created") == 2
    assert _usage.counter_value(db, b.id, TODAY, "tasks_created") == 1


@pytest.mark.req("Hosted readiness")
@pytest.mark.wp("P0-21")
@pytest.mark.xfail(strict=True, reason="spec:P0-21")
async def test_report_by_day_range(
    app: FastAPI, client: httpx.AsyncClient, db: DbUrls, workspace: WorkspaceHandle
) -> None:
    """T-P0-21-06
    Events at 2026-03-08T23:30Z and 2026-03-09T00:30Z count on two UTC days.
    `usage.api.report` over both days returns both rows, over one day only that day's;
    `GET /v1/usage?from=2026-03-08&to=2026-03-09` returns both rows to a signed-in session
    and 401 without one.
    """
    from tests.fixtures import make_envelope  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.core.tests.integration._audit import signed_in  # noqa: PLC0415
    from tumnis.modules.usage.api import record, report  # noqa: PLC0415

    for at in (datetime(2026, 3, 8, 23, 30, tzinfo=UTC), datetime(2026, 3, 9, 0, 30, tzinfo=UTC)):
        env = make_envelope("task.created", _task_payload(), workspace, occurred_at=at)
        async with tenant_session(workspace.ctx) as session:
            assert await record(session, env) == 1

    async with tenant_session(workspace.ctx) as session:
        both = await report(session, date(2026, 3, 8), date(2026, 3, 9))
        second = await report(session, date(2026, 3, 9), date(2026, 3, 9))
    assert [(r.day, r.counter, r.value) for r in both] == [
        (date(2026, 3, 8), "tasks_created", 1),
        (date(2026, 3, 9), "tasks_created", 1),
    ]
    assert [(r.day, r.counter, r.value) for r in second] == [(date(2026, 3, 9), "tasks_created", 1)]

    query = {"from": "2026-03-08", "to": "2026-03-09"}
    assert (await client.get("/v1/usage", params=query)).status_code == 401

    async with signed_in(app, workspace.id, uuid.uuid4()) as session_client:
        response = await session_client.get("/v1/usage", params=query)
    assert response.status_code == 200, response.text
    assert response.json() == [
        {"day": "2026-03-08", "counter": "tasks_created", "value": 1},
        {"day": "2026-03-09", "counter": "tasks_created", "value": 1},
    ]
