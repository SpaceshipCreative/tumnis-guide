"""T-P0-07-05 drain stall: two event loops must not share one AsyncEngine.

The worker runs its own loop (outbox relay, folder and vault watchers, cache listener) and
DBOS runs enqueued workflows on a background loop. Both used the same pooled engine, so a
watcher's first query and a workflow step's first query could race the engine's first
connection, which SQLAlchemy guards with an asyncio lock. The lock's waiter belonged to one
loop and was woken from the other loop's thread: without asyncio debug the waiting loop
never woke (the restarted worker's `resolve_dead_letter_if_any` step hung and the drain
timed out), with it the release raises "Non-thread-safe operation invoked on an event loop
other than the current one".
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from tumnis.core import db as core_db

if TYPE_CHECKING:
    from tests._pg import DbUrls

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

ROUNDS = 10  # each round races a fresh engine's first connection from two loops
JOIN_S = 15.0


def _race_first_connect() -> list[str]:
    """Two threads, each with its own event loop (asyncio debug on, so a cross-thread wake
    raises instead of hanging), start their first query at the same moment. Returns what
    went wrong: an exception per failed loop, or a loop that never finished."""
    start = threading.Barrier(2)
    problems: list[str] = []

    async def first_query() -> None:
        start.wait(JOIN_S)  # both loops reach the engine together
        try:
            async with core_db.app_sessionmaker()() as session:
                await session.execute(text("SELECT 1"))
        finally:
            await core_db.dispose()  # this loop's pooled connections

    def run() -> None:
        try:
            asyncio.run(asyncio.wait_for(first_query(), JOIN_S), debug=True)
        except BaseException as exc:  # reported, not raised in the thread
            problems.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=run, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(JOIN_S * 2)
        if thread.is_alive():
            problems.append("a loop never finished its first query")
    return problems


@pytest.mark.req("ADR-0002")
@pytest.mark.wp("P0-07")
@pytest.mark.xfail(strict=True, reason="bug: T-P0-07-05 drain stall, engines shared across loops")
def test_two_event_loops_make_their_first_query_at_once(
    db: DbUrls, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker's case: pooled engines, two loops on two threads, first query at once."""
    monkeypatch.setattr(core_db, "_state", core_db._State())  # restored after the test
    for _ in range(ROUNDS):
        core_db.configure(db.app, db.app)  # pooled, as `tumnis.worker` configures it
        assert _race_first_connect() == []
