"""SEED: a reset that races an in-flight request no longer waits forever.

The TRUNCATE locks every table in name order. A request that holds a late table in its
transaction and then reads an early one on a second connection (a lookup that opens its
own session, as `day_calendar` does inside the swap's transaction) waits on the reset
while the reset waits on it. Postgres cannot see that cycle (the request's transaction
waits in Python, not on a lock), so before this both waited for ever and every later reset
queued behind them (the e2e stall after J1's swap). The reset's lock wait is bounded: it
gives way, logs who held what, and tries again once the request has finished.
"""

from __future__ import annotations

import asyncio
import logging
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
@pytest.mark.wp("SEED")
@pytest.mark.xfail(strict=True, reason="spec:SEED")
async def test_reset_gives_way_to_a_request_it_blocks_unseen_and_tries_again(
    db: DbUrls, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """T-SEED-27
    A request's transaction holds `ACCESS SHARE` on the last table; the reset's TRUNCATE
    queues behind it; the request then reads the first table on a second connection and
    commits only once that read answers. The reset gives way at its lock timeout (logging
    the open transactions), the read answers, the request commits, and the reset's next
    attempt empties every table."""
    from tumnis.core import testing_routes  # noqa: PLC0415

    monkeypatch.setattr(testing_routes, "RESET_LOCK_TIMEOUT_S", 1)
    names = await asyncio.to_thread(_table_names, db)
    first, last = names[0], names[-1]
    request_tx = await psycopg.AsyncConnection.connect(db.libpq(SUPERUSER))
    lookup = await psycopg.AsyncConnection.connect(db.libpq(SUPERUSER))
    try:
        await request_tx.execute(f'LOCK TABLE "{last}" IN ACCESS SHARE MODE')
        with caplog.at_level(logging.WARNING, logger=testing_routes.__name__):
            reset = asyncio.create_task(testing_routes.truncate_tables(db.owner))
            for _ in range(500):
                if await asyncio.to_thread(_truncate_waits, db):
                    break
                await asyncio.sleep(0.02)
            else:
                reset.cancel()
                pytest.fail("the TRUNCATE never queued behind the request")
            # The request's own lookup: queues behind the TRUNCATE's lock on `first`.
            await asyncio.wait_for(
                lookup.execute(f'LOCK TABLE "{first}" IN ACCESS SHARE MODE'), timeout=30
            )
            await lookup.commit()
            await request_tx.commit()  # the request ends once its lookup answered
            emptied = await asyncio.wait_for(reset, timeout=30)
    finally:
        await lookup.close()
        await request_tx.close()

    assert first in emptied
    assert last in emptied
    assert any("reset blocked" in record.getMessage() for record in caplog.records)
