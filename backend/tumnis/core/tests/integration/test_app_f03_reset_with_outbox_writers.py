"""APP-F03 (final application test, was APP-04): `POST /v1/test/reset` deadlocked with
writers that emit, and answered 500 or timed out.

A writer changes its rows and then emits: it holds a table (here `workspaces`) and then
inserts into `outbox`. The reset locked `outbox` first and then TRUNCATEd the rest in name
order, the opposite order, so every collision was a deadlock (125 in one e2e run, and 12 of
13 e2e failures). The reset lost all ten of its attempts to a steady stream of such writers
and answered 500; some writers were cancelled as deadlock victims instead. The reset now
takes `outbox` last, as the writers do, and never waits long while holding a lock: every
reset answers 204 and no writer is a deadlock victim."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import psycopg
import pytest
from psycopg.errors import DeadlockDetected
from psycopg.types.json import Jsonb

from tests._pg import OWNER

if TYPE_CHECKING:
    import httpx

    from tests._pg import DbUrls

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

WRITERS = 4
HOLD_S = 0.05  # a writer's pause between its own row and its outbox row
RESETS = 3
AT = datetime(2026, 3, 9, 12, tzinfo=UTC)


async def _write_then_emit(
    db: DbUrls, stop: asyncio.Event, under_way: asyncio.Event, tally: dict[str, int]
) -> None:
    """One writer: insert a workspace, pause, emit an event for it, commit; again until
    `stop`. Counts commits and deadlocks (`under_way` once the writers committed WRITERS
    times); any other error is raised."""
    async with await psycopg.AsyncConnection.connect(db.libpq(OWNER)) as conn:
        while not stop.is_set():
            try:
                async with conn.transaction():
                    row = await (
                        await conn.execute(
                            "INSERT INTO workspaces (name, timezone)"
                            " VALUES (%s, 'America/New_York') RETURNING id",
                            (f"writer {uuid.uuid4()}",),
                        )
                    ).fetchone()
                    assert row is not None
                    await asyncio.sleep(HOLD_S)
                    await conn.execute(
                        "INSERT INTO outbox (workspace_id, name, schema_version, actor,"
                        " occurred_at, payload) VALUES (%s, 'test.ping', 1, 'system', %s, %s)",
                        (row[0], AT, Jsonb({"schema_version": 1, "note": "APP-F03"})),
                    )
            except DeadlockDetected:
                tally["deadlocks"] += 1
                continue
            tally["commits"] += 1
            if tally["commits"] >= WRITERS:
                under_way.set()


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
@pytest.mark.xfail(strict=True, reason="spec:FIX-app-final-minor")
async def test_app_f03_reset_answers_204_while_writers_emit(
    db: DbUrls, client: httpx.AsyncClient
) -> None:
    """Four writers keep writing a row and then emitting for it while three resets run one
    after another: every reset answers 204, the writers keep committing, and none of them
    is cancelled as a deadlock victim."""
    stop, under_way = asyncio.Event(), asyncio.Event()
    tally = {"commits": 0, "deadlocks": 0}
    writers = [
        asyncio.create_task(_write_then_emit(db, stop, under_way, tally)) for _ in range(WRITERS)
    ]
    statuses: list[int] = []
    try:
        await asyncio.wait_for(under_way.wait(), timeout=30)
        started = tally["commits"]
        for _ in range(RESETS):
            response = await asyncio.wait_for(client.post("/v1/test/reset"), timeout=90)
            statuses.append(response.status_code)
            if response.status_code != 204:
                break  # red at base: one failed reset is enough
    finally:
        stop.set()
        results = await asyncio.wait_for(
            asyncio.gather(*writers, return_exceptions=True), timeout=30
        )

    assert statuses == [204] * RESETS
    assert [r for r in results if isinstance(r, BaseException)] == []
    assert tally["deadlocks"] == 0
    assert tally["commits"] > started  # the writers kept writing through the resets
