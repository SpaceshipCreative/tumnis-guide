"""The outbox relay: every event reaches every subscriber once, across relay and worker
crashes, woken by NOTIFY with a polling backstop (P0-07, ADR-0011, ADR-0002, REL-3).

Subscribers come from `_deliveries` (testa/testb on `test.ping`, testa.solo on `test.solo`).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests._pg import OWNER

if TYPE_CHECKING:
    from dbos import DBOS, WorkflowHandleAsync

    from tests._pg import DbUrls
    from tests.fixtures import WorkerKillerFactory, WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

AT = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
PING_SUBSCRIBERS = ("testa.record", "testb.record")


@pytest.fixture
async def core_db(db: DbUrls) -> AsyncIterator[None]:
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    try:
        yield
    finally:
        await core_db.dispose()


async def _emit_pings(ctx: Any, n: int) -> list[uuid.UUID]:
    from tumnis.core.events import TestPingV1  # noqa: PLC0415
    from tumnis.core.outbox import emit  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    async with tenant_session(ctx) as session:
        return [await emit(session, TestPingV1(note=f"n{i}"), occurred_at=AT) for i in range(n)]


def _unsent(db: DbUrls) -> int:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        row = conn.execute("SELECT count(*) FROM outbox WHERE sent_at IS NULL").fetchone()
    assert row is not None
    return int(row[0])


async def _wait_for_deliveries(db: DbUrls, expected: int, timeout_s: float) -> float:
    """Seconds until test_deliveries holds `expected` rows; fails after `timeout_s`."""
    from tumnis.core.tests.integration import _deliveries  # noqa: PLC0415

    start = time.monotonic()
    while True:
        total = sum(_deliveries.counts(db.libpq(OWNER)).values())
        elapsed = time.monotonic() - start
        if total >= expected:
            return elapsed
        if elapsed > timeout_s:
            pytest.fail(f"{total} of {expected} deliveries after {timeout_s} s")
        await asyncio.sleep(0.02)


@pytest.mark.req("ADR-0011", "REL-3")
@pytest.mark.wp("P0-07")
async def test_each_event_reaches_each_subscriber_once(
    db: DbUrls, dbos: type[DBOS], workspace: WorkspaceHandle
) -> None:
    """T-P0-07-03
    3 events x 2 subscribers: 6 deliveries, `sent_at` set on all rows.
    """
    from tumnis.core.events import relay_once  # noqa: PLC0415
    from tumnis.core.tests.integration import _deliveries  # noqa: PLC0415

    _deliveries.create_table(db.libpq(OWNER))
    event_ids = await _emit_pings(workspace.ctx, 3)

    assert await relay_once() == 3
    for event_id in event_ids:
        for sub in PING_SUBSCRIBERS:
            handle: WorkflowHandleAsync[str] = await dbos.retrieve_workflow_async(
                f"{event_id}:{sub}"
            )
            assert await handle.get_result() == "delivered"

    assert _deliveries.counts(db.libpq(OWNER)) == {
        (event_id, sub): 1 for event_id in event_ids for sub in PING_SUBSCRIBERS
    }
    assert _unsent(db) == 0


@pytest.mark.req("ADR-0011", "REL-3")
@pytest.mark.wp("P0-07")
async def test_worker_killed_between_enqueue_and_mark_sent_delivers_once(
    db: DbUrls, worker_killer: WorkerKillerFactory
) -> None:
    """T-P0-07-04
    Kill at `relay.after_enqueue`, restart: each subscriber ran once per event.
    """
    from tumnis.core.tests.integration import _deliveries  # noqa: PLC0415

    killer = worker_killer("relay.after_enqueue")
    assert await killer.run_until_killed() == 137
    assert _unsent(db) == 5  # killed before mark-sent

    await killer.restart_and_drain(timeout_s=30)

    assert _unsent(db) == 0
    assert _deliveries.counts(db.libpq(OWNER)) == {
        (event_id, sub): 1 for event_id in killer.event_ids for sub in PING_SUBSCRIBERS
    }


@pytest.mark.req("ADR-0002")
@pytest.mark.wp("P0-07")
async def test_worker_killed_after_handler_step_does_not_rerun_it(
    db: DbUrls, worker_killer: WorkerKillerFactory
) -> None:
    """T-P0-07-05
    Kill at `deliver.after_handler`, restart: the workflow finishes without a second handler
    call. One event with one subscriber (`test.solo`), so no sibling workflow is caught
    mid-handler by the kill (a crash inside a handler re-runs it; that is at-least-once).
    """
    from tumnis.core.tests.integration import _deliveries  # noqa: PLC0415

    killer = worker_killer("deliver.after_handler", events=1, event="test.solo")
    assert await killer.run_until_killed() == 137
    (event_id,) = killer.event_ids
    assert _deliveries.counts(db.libpq(OWNER)) == {(event_id, "testa.solo"): 1}

    outputs = await killer.restart_and_drain(timeout_s=30)

    assert outputs == {f"{event_id}:testa.solo": "delivered"}
    assert _deliveries.counts(db.libpq(OWNER)) == {(event_id, "testa.solo"): 1}


@pytest.mark.req("ADR-0011")
@pytest.mark.wp("P0-07")
@pytest.mark.usefixtures("core_db")
async def test_concurrent_relays_claim_disjoint_rows(
    db: DbUrls, workspace: WorkspaceHandle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-P0-07-06
    Two `relay_once` calls in parallel on 200 rows: each row claimed by exactly one.
    The enqueue is replaced by a recorder that yields, so each relay holds its claim open
    while the other claims.
    """
    from dbos import DBOS  # noqa: PLC0415

    from tumnis.core import events  # noqa: PLC0415
    from tumnis.core.tests.integration import _deliveries  # noqa: F401, PLC0415

    claimed: dict[int, set[uuid.UUID]] = {}

    async def record_enqueue(_queue: str, _fn: Any, _sub: str, envelope: dict[str, Any]) -> None:
        task = id(asyncio.current_task())
        claimed.setdefault(task, set()).add(uuid.UUID(envelope["event_id"]))
        await asyncio.sleep(0.001)

    monkeypatch.setattr(DBOS, "enqueue_workflow_async", record_enqueue)
    event_ids = set(await _emit_pings(workspace.ctx, 200))

    first, second = await asyncio.gather(events.relay_once(), events.relay_once())

    assert (first, second) == (100, 100)
    a, b = claimed.values()
    assert a.isdisjoint(b)
    assert a | b == event_ids
    assert _unsent(db) == 0


async def _run_relay(poll_s: float) -> tuple[asyncio.Event, asyncio.Task[None]]:
    from tumnis.core.events import relay_forever  # noqa: PLC0415

    stop = asyncio.Event()
    task = asyncio.create_task(relay_forever(stop, poll_s=poll_s))
    await asyncio.sleep(0.5)  # connected, LISTENing, first drain done, waiting
    assert not task.done(), task
    return stop, task


async def _stop_relay(stop: asyncio.Event, task: asyncio.Task[None]) -> None:
    stop.set()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.req("ADR-0011")
@pytest.mark.wp("P0-07")
async def test_notify_wakes_relay_before_poll(
    db: DbUrls, dbos: type[DBOS], workspace: WorkspaceHandle
) -> None:
    """T-P0-07-07
    With `poll_s=30`, an emitted event is delivered within 1 s.
    """
    from tumnis.core.tests.integration import _deliveries  # noqa: PLC0415

    _deliveries.create_table(db.libpq(OWNER))
    stop, task = await _run_relay(poll_s=30)
    try:
        await _emit_pings(workspace.ctx, 1)
        elapsed = await _wait_for_deliveries(db, expected=2, timeout_s=5)
    finally:
        await _stop_relay(stop, task)
    assert elapsed < 1.0


@pytest.mark.req("ADR-0011")
@pytest.mark.wp("P0-07")
async def test_poll_backstop_delivers_without_notify(
    db: DbUrls, dbos: type[DBOS], workspace: WorkspaceHandle
) -> None:
    """T-P0-07-08
    A row inserted by the owner with no NOTIFY is delivered within `poll_s + 1` s
    (`poll_s=0.2`).
    """
    from tumnis.core.tests.integration import _deliveries  # noqa: PLC0415

    _deliveries.create_table(db.libpq(OWNER))
    stop, task = await _run_relay(poll_s=0.2)
    try:
        with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
            conn.execute(
                "INSERT INTO outbox (workspace_id, name, schema_version, actor, occurred_at,"
                " payload) VALUES (%s, 'test.ping', 1, 'system', %s, %s)",
                (workspace.id, AT, Jsonb({"schema_version": 1, "note": "quiet"})),
            )
        elapsed = await _wait_for_deliveries(db, expected=2, timeout_s=5)
    finally:
        await _stop_relay(stop, task)
    assert elapsed < 0.2 + 1.0
