"""P0-29: `POST /v1/test/reset` retries when Postgres picks its TRUNCATE as a deadlock victim.

The TRUNCATE locks every table in name order. A reader that holds a lock on a late table
and then reads an early one waits on the reset while the reset waits on it: a deadlock
Postgres sees, and it aborts the TRUNCATE (the first waiter checks first). The Performance
job's load-set resets hit this once in CI (a 500 from the reset); the reader finishes as
soon as the TRUNCATE gives way, so a retry empties the tables.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import OWNER, SUPERUSER

if TYPE_CHECKING:
    from tests._pg import DbUrls

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _table_names(db: DbUrls) -> list[str]:
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            " AND tablename NOT IN ('outbox', 'deployment_marker')"
            " AND tablename NOT LIKE 'alembic_version%'"
        ).fetchall()
    return sorted(str(name) for (name,) in rows)  # the order truncate_tables locks in


def _truncate_waits(db: DbUrls) -> bool:
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        row = conn.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
            " AND wait_event_type = 'Lock' AND query LIKE 'TRUNCATE%'"
        ).fetchone()
    return row is not None and row[0] > 0


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-29")
async def test_reset_retries_after_postgres_picks_it_as_deadlock_victim(db: DbUrls) -> None:
    """A reader holds `ACCESS SHARE` on the last table, the reset's TRUNCATE queues behind
    it, then the reader asks for the first table: Postgres aborts the TRUNCATE; the reset
    tries again once the reader commits and returns the emptied tables."""
    from tumnis.core.testing_routes import truncate_tables  # noqa: PLC0415

    names = await asyncio.to_thread(_table_names, db)
    first, last = names[0], names[-1]
    # The superuser may raise its own deadlock_timeout: the TRUNCATE (default 1 s) then
    # always finds the cycle first and is the victim, as in CI.
    reader = await psycopg.AsyncConnection.connect(db.libpq(SUPERUSER))
    try:
        await reader.execute("SET deadlock_timeout = '10s'")
        await reader.execute(f'LOCK TABLE "{last}" IN ACCESS SHARE MODE')
        reset = asyncio.create_task(truncate_tables(db.owner))
        for _ in range(500):
            if await asyncio.to_thread(_truncate_waits, db):
                break
            await asyncio.sleep(0.02)
        else:
            reset.cancel()
            pytest.fail("the TRUNCATE never queued behind the reader")
        # Waits on the TRUNCATE's lock on `first` until Postgres ends the cycle.
        await reader.execute(f'LOCK TABLE "{first}" IN ACCESS SHARE MODE')
        await reader.commit()
        emptied = await asyncio.wait_for(reset, timeout=30)
    finally:
        await reader.close()

    assert first in emptied
    assert last in emptied
