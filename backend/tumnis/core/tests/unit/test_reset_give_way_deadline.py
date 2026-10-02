"""APP-F03 (CodeRabbit on #183): a reset that keeps giving way stops at one deadline.

The reset's TRUNCATE gives way when a lock is not granted within RESET_LOCK_TIMEOUT_S,
pauses a jittered moment and tries again. Counting only the lock timeouts let the pauses,
the reports and any locks granted late in an attempt run on past the budget. The reset
now answers 503 once LOCK_WAIT_BUDGET_S have gone by since it began, by the clock, and no
pause runs past that deadline."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError

pytestmark = [pytest.mark.req("REL-7"), pytest.mark.wp("SEED")]

OWNER_URL = "postgresql+psycopg://owner@db.invalid/tumnis"  # never connected to


class _LockNotAvailableError(Exception):
    sqlstate = "55P03"


async def test_app_f03_reset_gives_up_at_its_deadline_whatever_the_pauses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each attempt waits 0.6 s in all (two locks granted late, then a timeout) and each
    pause draws the longest jitter: the reset still raises ResetBlockedError once the
    budget has gone by, and no pause ends past the deadline."""
    routes: Any = importlib.import_module("tumnis.core.testing_routes")
    now = [1000.0]
    pause_ends: list[float] = []

    async def truncate_once(_engine: Any) -> list[str]:
        now[0] += 0.6
        raise DBAPIError("TRUNCATE", {}, _LockNotAvailableError("lock timeout"))

    async def pause(seconds: float) -> None:
        now[0] += seconds
        pause_ends.append(now[0])

    async def open_transactions(_engine: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(routes, "_truncate_once", truncate_once)
    monkeypatch.setattr(routes, "_deadlock_pause", pause)
    monkeypatch.setattr(routes, "_open_transactions", open_transactions)
    monkeypatch.setattr(routes, "_monotonic", lambda: now[0])
    monkeypatch.setattr(routes.random, "random", lambda: 1.0)

    with pytest.raises(routes.ResetBlockedError):
        await routes.truncate_tables(OWNER_URL)

    deadline = 1000.0 + routes.LOCK_WAIT_BUDGET_S
    assert pause_ends
    assert all(end <= deadline for end in pause_ends)
    assert now[0] <= deadline + 0.6  # at most the attempt that crossed the deadline
