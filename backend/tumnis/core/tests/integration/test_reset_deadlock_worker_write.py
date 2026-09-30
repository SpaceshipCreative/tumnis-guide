"""`POST /v1/test/reset` survives a deadlock with a worker transaction (P1-07 e2e).

The reset locks `outbox` first (issue #51), then TRUNCATEs the rest. A worker transaction
that has written a table (say `tasks`, the quick-add label) and then emits waits on the
reset's `outbox` lock while the reset waits on its `tasks` lock: Postgres sees the cycle
and cancels one of them. When the reset is the one cancelled, it tries again.
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

import psycopg
import pytest
from psycopg.errors import DeadlockDetected

from tests._pg import OWNER, SUPERUSER

if TYPE_CHECKING:
    from tests._pg import DbUrls

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

# Scenarios to try before giving up on the reset ever being the deadlock's victim.
SCENARIOS = 3


def _truncate_waiting(monitor: psycopg.Connection) -> bool:
    rows = monitor.execute(
        "SELECT query FROM pg_stat_activity WHERE datname = current_database()"
        " AND wait_event_type = 'Lock'"
    ).fetchall()
    return any("TRUNCATE" in str(query) for (query,) in rows)


async def _scenario(
    db: DbUrls, monitor: psycopg.Connection, emitted: threading.Event
) -> list[str] | None:
    """One run of the cycle: the reset's emptied tables when the reset was the victim (the
    worker's emit went through), None when Postgres cancelled the worker instead.

    Postgres checks a lock wait for a deadlock once, `deadlock_timeout` after the wait
    starts, and cancels the waiter that finds the cycle. The worker's longer timeout (10 s,
    superuser only) leaves that to the reset (1 s) as long as the worker asks for `outbox`
    within the reset's first second of waiting; one persistent monitor connection keeps
    that gap to a few milliseconds. On a runner slow enough to miss it, the worker is
    cancelled and the scenario runs again."""
    from tumnis.core.testing_routes import truncate_tables  # noqa: PLC0415

    worker = psycopg.connect(db.libpq(SUPERUSER))
    worker.execute("SET deadlock_timeout = '10s'")
    worker.execute("LOCK TABLE tasks IN ROW EXCLUSIVE MODE")  # as an UPDATE tasks would
    reset = asyncio.create_task(truncate_tables(db.owner))
    try:
        for _ in range(1000):
            if await asyncio.to_thread(_truncate_waiting, monitor):
                break
            await asyncio.sleep(0.005)
        else:
            pytest.fail("the reset never waited for the worker's lock on tasks")

        def emit_then_commit() -> None:
            worker.execute("LOCK TABLE outbox IN ROW EXCLUSIVE MODE")  # as emit's INSERT
            emitted.set()
            worker.commit()

        try:
            await asyncio.wait_for(asyncio.to_thread(emit_then_commit), timeout=30)
        except DeadlockDetected:
            worker.rollback()  # the worker was the victim; the reset now finishes
            await asyncio.wait_for(reset, timeout=30)
            return None
        return await asyncio.wait_for(reset, timeout=30)
    finally:
        if not reset.done():
            reset.cancel()
        worker.close()


@pytest.mark.req("REL-7")
@pytest.mark.wp("P1-07")
async def test_reset_retries_after_a_deadlock_with_a_worker_write(db: DbUrls) -> None:
    """A transaction holding `tasks` asks for `outbox` while the reset holds `outbox` and
    waits for `tasks`. The reset, the first to check (the worker's deadlock_timeout is
    longer), is the deadlock's victim; it retries once the worker's transaction commits,
    and empties the tables."""
    emitted = threading.Event()
    emptied: list[str] = []
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as monitor:
        for _ in range(SCENARIOS):
            result = await _scenario(db, monitor, emitted)
            if result is not None:
                emptied = result
                break
        else:
            pytest.fail(f"Postgres cancelled the worker, not the reset, {SCENARIOS} times")

    assert emitted.is_set()
    assert {"outbox", "tasks"} <= set(emptied)
