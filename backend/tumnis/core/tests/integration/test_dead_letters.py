"""A failing subscriber retries with jittered backoff, then dead-letters on its own; a dead
letter can be retried once or discarded (P0-07, REL-3).

Subscribers come from `_deliveries`: on `test.flaky`, `testa.flaky_ok` always records and
`testb.flaky_fail` (max_attempts=3, base 0.05 s, cap 0.2 s) raises `RuntimeError("boom")`
while `FLAKY.failing` is set.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    import httpx
    from dbos import DBOS, DBOSClient, WorkflowHandleAsync

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

AT = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


@pytest.fixture
def flaky() -> Iterator[Any]:
    """The failing subscriber's switch, failing at the start of every test."""
    from tumnis.core.tests.integration import _deliveries  # noqa: PLC0415

    _deliveries.FLAKY.failing = True
    try:
        yield _deliveries.FLAKY
    finally:
        _deliveries.FLAKY.failing = True


def _dead_letters(db: DbUrls) -> list[dict[str, Any]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        cur = conn.execute("SELECT * FROM dead_letters ORDER BY created_at")
        names = [c.name for c in cur.description or ()]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


async def _dead_letter_one(db: DbUrls, dbos: type[DBOS], ws: WorkspaceHandle) -> uuid.UUID:
    """Emit one `test.flaky` event, relay it and wait for both workflows; returns event_id."""
    from tumnis.core.events import relay_once  # noqa: PLC0415
    from tumnis.core.outbox import emit  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.core.tests.integration import _deliveries  # noqa: PLC0415

    _deliveries.create_table(db.libpq(OWNER))
    async with tenant_session(ws.ctx) as session:
        event_id = await emit(session, _deliveries.FlakyV1(note="x"), occurred_at=AT)
    assert await relay_once() == 1
    ok: WorkflowHandleAsync[str] = await dbos.retrieve_workflow_async(
        f"{event_id}:{_deliveries.FLAKY_OK}"
    )
    failed: WorkflowHandleAsync[str] = await dbos.retrieve_workflow_async(
        f"{event_id}:{_deliveries.FLAKY_FAIL}"
    )
    assert await ok.get_result() == "delivered"
    assert await failed.get_result() == "dead_lettered"
    return event_id


def _step(info: Any) -> str:
    return str(info["function_name"]).rsplit(".", 1)[-1]


@pytest.mark.req("REL-3")
@pytest.mark.wp("P0-07")
async def test_failing_subscriber_retries_with_jittered_backoff_then_dead_letters(
    db: DbUrls, dbos: type[DBOS], workspace: WorkspaceHandle, flaky: Any
) -> None:
    """T-P0-07-09
    3 attempts, delays within full-jitter bounds, one `open` dead-letter row with the last
    error.
    """
    from tumnis.core.tests.integration import _deliveries  # noqa: PLC0415

    event_id = await _dead_letter_one(db, dbos, workspace)

    steps = await dbos.list_workflow_steps_async(f"{event_id}:{_deliveries.FLAKY_FAIL}")
    handler_runs = [s for s in steps if _step(s) == "run_handler"]
    delays: list[Any] = [s["output"] for s in steps if _step(s) == "backoff_delay"]
    assert len(handler_runs) == 3
    assert all(isinstance(s["error"], RuntimeError) for s in handler_runs), handler_runs
    assert len(delays) == 2
    assert 0.0 <= delays[0] <= 0.05
    assert 0.0 <= delays[1] <= 0.1

    (row,) = _dead_letters(db)
    assert row["workspace_id"] == workspace.id
    assert row["event_id"] == event_id
    assert row["subscriber"] == _deliveries.FLAKY_FAIL
    assert row["event_name"] == "test.flaky"
    assert row["envelope"]["event_id"] == str(event_id)
    assert row["attempts"] == 3
    assert row["retries"] == 0
    assert row["error"] == "RuntimeError: boom"
    assert row["status"] == "open"
    assert row["last_at"] is not None


@pytest.mark.req("REL-3")
@pytest.mark.wp("P0-07")
async def test_sibling_subscribers_complete_when_one_fails(
    db: DbUrls, dbos: type[DBOS], workspace: WorkspaceHandle, flaky: Any
) -> None:
    """T-P0-07-10
    The healthy subscriber of the same event is delivered once while the other dead-letters.
    """
    from tumnis.core.tests.integration import _deliveries  # noqa: PLC0415

    event_id = await _dead_letter_one(db, dbos, workspace)

    assert _deliveries.counts(db.libpq(OWNER)) == {(event_id, _deliveries.FLAKY_OK): 1}
    assert [(r["event_id"], r["subscriber"]) for r in _dead_letters(db)] == [
        (event_id, _deliveries.FLAKY_FAIL)
    ]


@pytest.mark.req("REL-3")
@pytest.mark.wp("P0-07")
async def test_retrying_a_dead_letter_runs_it_once(
    db: DbUrls,
    dbos: type[DBOS],
    dbos_client: DBOSClient,
    workspace: WorkspaceHandle,
    flaky: Any,
) -> None:
    """T-P0-07-11
    Two concurrent `retry` calls: one wins, the other gets 409; one extra handler run;
    status `resolved` after success.
    """
    from tumnis.core import deadletter  # noqa: PLC0415
    from tumnis.core.tests.integration import _deliveries  # noqa: PLC0415

    deadletter.use_client(dbos_client)
    event_id = await _dead_letter_one(db, dbos, workspace)
    page = await deadletter.list_dead_letters(workspace.ctx)
    (item,) = page.items
    assert (item.event_id, item.status, item.attempts) == (event_id, "open", 3)

    flaky.failing = False
    results = await asyncio.gather(
        deadletter.retry(workspace.ctx, item.id, expected_version=item.version),
        deadletter.retry(workspace.ctx, item.id, expected_version=item.version),
        return_exceptions=True,
    )

    wins = [r for r in results if isinstance(r, deadletter.DeadLetterOut)]
    losses = [r for r in results if isinstance(r, BaseException)]
    assert len(wins) == 1, results
    assert (wins[0].status, wins[0].retries) == ("retrying", 1)
    assert len(losses) == 1, results
    assert isinstance(losses[0], deadletter.StaleVersion | deadletter.DeadLetterNotOpen)
    assert losses[0].status == 409

    retry_id = f"{event_id}:{_deliveries.FLAKY_FAIL}:retry:1"
    handle: WorkflowHandleAsync[str] = await dbos.retrieve_workflow_async(retry_id)
    assert await handle.get_result() == "delivered"
    assert _deliveries.counts(db.libpq(OWNER)) == {
        (event_id, _deliveries.FLAKY_OK): 1,
        (event_id, _deliveries.FLAKY_FAIL): 1,
    }
    (row,) = _dead_letters(db)
    assert (row["status"], row["retries"]) == ("resolved", 1)


@pytest.mark.req("REL-3")
@pytest.mark.wp("P0-07")
async def test_discard_closes_the_item(
    db: DbUrls,
    dbos: type[DBOS],
    dbos_client: DBOSClient,
    workspace: WorkspaceHandle,
    flaky: Any,
) -> None:
    """T-P0-07-12
    `discard` sets `discarded`; a later `retry` returns 409 `dead_letter_not_open`.
    """
    from tumnis.core import deadletter  # noqa: PLC0415

    deadletter.use_client(dbos_client)
    await _dead_letter_one(db, dbos, workspace)
    (item,) = (await deadletter.list_dead_letters(workspace.ctx)).items

    discarded = await deadletter.discard(workspace.ctx, item.id, expected_version=item.version)
    assert discarded.status == "discarded"
    assert discarded.version > item.version
    assert (await deadletter.list_dead_letters(workspace.ctx)).items == []

    with pytest.raises(deadletter.DeadLetterNotOpen) as refused:
        await deadletter.retry(workspace.ctx, item.id, expected_version=discarded.version)
    assert (refused.value.status, refused.value.code) == (409, "dead_letter_not_open")
    (row,) = _dead_letters(db)
    assert (row["status"], row["retries"]) == ("discarded", 0)


@pytest.mark.req("REL-3")
@pytest.mark.wp("P0-07")
async def test_dead_letter_routes_refuse_without_a_session(client: httpx.AsyncClient) -> None:
    """The /v1/dead-letters routes are mounted and session-only: with no signed-in session
    (P0-13 supplies it) every call is 401, never a listing or a write."""
    item = uuid.uuid4()
    responses = [
        await client.get("/v1/dead-letters"),
        await client.post(f"/v1/dead-letters/{item}/retry", json={"version": 1}),
        await client.post(f"/v1/dead-letters/{item}/discard", json={"version": 1}),
    ]
    assert [r.status_code for r in responses] == [401, 401, 401]
