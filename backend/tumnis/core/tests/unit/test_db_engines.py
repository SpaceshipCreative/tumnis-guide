"""Engines per event loop (P0-02): a process that runs two event loops, like the worker
(its own loop for the relay and the watchers, DBOS's loop for enqueued workflows), must
not share one AsyncEngine between them. The T-P0-07-05 drain stall was the two loops racing
the engine's first connection on one SQLAlchemy mutex."""

import asyncio
import threading

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

from tumnis.core import db

URL = "postgresql+psycopg://tumnis_app:secret@db.invalid:5432/tumnis"


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "_state", db._State())  # restored after the test
    db.configure(URL, URL)


async def _engines() -> tuple[AsyncEngine, AsyncEngine, AsyncEngine]:
    return db.app_engine(), db.app_engine(), db.direct_engine()


@pytest.mark.req("ADR-0002")
@pytest.mark.wp("P0-07")
def test_each_event_loop_gets_its_own_engine() -> None:
    """One engine per (event loop, role): the same loop gets the same engine back; another
    loop, here on another thread as DBOS's is, gets its own."""
    first, again, direct = asyncio.run(_engines())
    assert first is again
    assert direct is not first

    other: list[AsyncEngine] = []
    thread = threading.Thread(target=lambda: other.extend(asyncio.run(_engines())))
    thread.start()
    thread.join(10)
    assert len(other) == 3
    assert other[0] is other[1]
    assert other[0] is not first
    assert other[2] is not direct


def _limits(engine: AsyncEngine) -> tuple[int, int]:
    """(pool_size, max_overflow) of an engine's pool."""
    pool = engine.pool
    assert isinstance(pool, AsyncAdaptedQueuePool)
    return pool.size(), getattr(pool, "_max_overflow", -1)  # no public reader


def _in_thread_loop() -> tuple[asyncio.AbstractEventLoop, threading.Thread]:
    """A running event loop on a thread of its own, as DBOS's background loop is."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    return loop, thread


def _stop(loop: asyncio.AbstractEventLoop, thread: threading.Thread) -> None:
    loop.call_soon_threadsafe(loop.stop)
    thread.join(10)
    loop.close()


@pytest.mark.req("ADR-0002")
@pytest.mark.wp("P0-07")
def test_a_loop_can_keep_smaller_pools() -> None:
    """The worker's own loop serves only the relay and the watchers: `limit_pools` caps the
    engines it builds there, and leaves every other loop's at SQLAlchemy's defaults."""

    async def limited() -> AsyncEngine:
        db.limit_pools(pool_size=2, max_overflow=1)
        return db.app_engine()

    assert _limits(asyncio.run(limited())) == (2, 1)
    assert _limits(asyncio.run(_engines())[0]) == (5, 10)  # SQLAlchemy's QueuePool defaults


@pytest.mark.req("ADR-0002")
@pytest.mark.wp("P0-07")
def test_dispose_closes_only_the_running_loops_engines() -> None:
    """Each loop disposes what it built: `dispose` closes the running loop's pools and the
    next use there builds a fresh engine, while another loop's engine, which may be in use
    on that loop, is left alone."""
    other, thread = _in_thread_loop()
    try:
        theirs = asyncio.run_coroutine_threadsafe(_engines(), other).result(10)[0]
        theirs_pool = theirs.pool

        async def dispose_here() -> tuple[AsyncEngine, AsyncEngine]:
            mine = db.app_engine()
            mine_pool = mine.pool
            await db.dispose()
            assert mine.pool is not mine_pool  # AsyncEngine.dispose() swaps in a new pool
            return mine, db.app_engine()

        mine, fresh = asyncio.run(dispose_here())
        assert fresh is not mine
        assert theirs.pool is theirs_pool
        assert asyncio.run_coroutine_threadsafe(_engines(), other).result(10)[0] is theirs
    finally:
        _stop(other, thread)


@pytest.mark.req("ADR-0002")
@pytest.mark.wp("P0-07")
def test_an_engine_built_outside_a_loop_keeps_no_pool() -> None:
    """Code outside any running loop gets one engine, which any loop may then use: it keeps
    no pool (SQLAlchemy: a NullPool engine may be shared between loops), while a loop's own
    engine stays pooled."""
    unbound = db.app_engine()
    assert db.app_engine() is unbound
    assert isinstance(unbound.pool, NullPool)
    assert isinstance(asyncio.run(_engines())[0].pool, AsyncAdaptedQueuePool)
