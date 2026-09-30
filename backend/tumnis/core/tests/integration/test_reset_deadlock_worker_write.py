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

from tests._pg import OWNER, SUPERUSER

if TYPE_CHECKING:
    from tests._pg import DbUrls

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _truncate_waiting(db: DbUrls) -> bool:
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        rows = conn.execute(
            "SELECT query FROM pg_stat_activity WHERE datname = current_database()"
            " AND wait_event_type = 'Lock'"
        ).fetchall()
    return any("TRUNCATE" in str(query) for (query,) in rows)


@pytest.mark.req("REL-7")
@pytest.mark.wp("P1-07")
async def test_reset_retries_after_a_deadlock_with_a_worker_write(db: DbUrls) -> None:
    """A transaction holding `tasks` asks for `outbox` while the reset holds `outbox` and
    waits for `tasks`. The reset, the first to check (the worker's deadlock_timeout is
    longer), is the deadlock's victim; it retries once the worker's transaction commits,
    and empties the tables."""
    from tumnis.core.testing_routes import truncate_tables  # noqa: PLC0415

    worker = psycopg.connect(db.libpq(SUPERUSER))
    # Postgres runs the deadlock check in whichever waiter's deadlock_timeout ends first,
    # and that waiter is the one cancelled. The worker's longer timeout (superuser only)
    # makes the reset, waiting at the default 1 s, always the victim.
    worker.execute("SET deadlock_timeout = '10s'")
    worker.execute("LOCK TABLE tasks IN ROW EXCLUSIVE MODE")  # as an UPDATE tasks would
    reset = asyncio.create_task(truncate_tables(db.owner))
    try:
        for _ in range(500):
            if await asyncio.to_thread(_truncate_waiting, db):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("the reset never waited for the worker's lock on tasks")
        emitted = threading.Event()

        def emit_then_commit() -> None:
            worker.execute("LOCK TABLE outbox IN ROW EXCLUSIVE MODE")  # as emit's INSERT
            emitted.set()
            worker.commit()

        await asyncio.wait_for(asyncio.to_thread(emit_then_commit), timeout=30)
        emptied = await asyncio.wait_for(reset, timeout=30)
    finally:
        if not reset.done():
            reset.cancel()
        worker.close()

    assert emitted.is_set()
    assert {"outbox", "tasks"} <= set(emptied)
