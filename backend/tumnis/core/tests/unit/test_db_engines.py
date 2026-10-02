"""Engines per event loop (P0-02): a process that runs two event loops, like the worker
(its own loop for the relay and the watchers, DBOS's loop for enqueued workflows), must
not share one AsyncEngine between them. The T-P0-07-05 drain stall was the two loops racing
the engine's first connection on one SQLAlchemy mutex."""

import asyncio
import threading

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

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
@pytest.mark.xfail(strict=True, reason="bug: T-P0-07-05 drain stall, engines shared across loops")
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
