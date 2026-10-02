"""APP-04 (application test): `POST /v1/test/reset` answered 500 after losing three
deadlocks in a row against in-flight writers.

The reset locks `outbox` first (issue #51) and then TRUNCATEs every table; a transaction
that wrote a table and then emits waits on `outbox` while the reset waits on its table, and
Postgres cancels one of them. Under the e2e suite's load the reset lost that race on all
three of its attempts (4 failed resets in one run), so the next test started on a 500. The
reset now keeps trying through a longer run of deadlocks, pausing a jittered moment between
attempts so the writer it collided with can commit first."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError

from tumnis.core import testing_routes

pytestmark = [pytest.mark.req("REL-7"), pytest.mark.wp("P0-29")]

OWNER_URL = "postgresql+psycopg://owner@db.invalid/tumnis"  # never connected to


class _DeadlockError(Exception):
    sqlstate = testing_routes.DEADLOCK_DETECTED


def _deadlock() -> DBAPIError:
    return DBAPIError("TRUNCATE", {}, _DeadlockError("deadlock detected"))


async def test_reset_survives_a_run_of_deadlocks_with_writers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Five deadlocks in a row (APP-04 saw three) and then a clean TRUNCATE: the reset
    empties the tables instead of failing, and pauses before every retry."""
    losses = 5
    attempts: list[int] = []
    pauses: list[float] = []

    async def truncate_once(_engine: Any) -> list[str]:
        attempts.append(len(attempts) + 1)
        if len(attempts) <= losses:
            raise _deadlock()
        return ["outbox", "tasks"]

    async def pause(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr(testing_routes, "_truncate_once", truncate_once)
    monkeypatch.setattr(testing_routes, "_deadlock_pause", pause)

    emptied = await testing_routes.truncate_tables(OWNER_URL)

    assert emptied == ["outbox", "tasks"]
    assert len(attempts) == losses + 1
    assert len(pauses) == losses
    assert all(0 <= seconds <= testing_routes.DEADLOCK_PAUSE_CAP_S for seconds in pauses)


async def test_reset_still_gives_up_after_its_last_deadlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deadlock on every attempt still ends: the last one is raised (a 500), after
    `DEADLOCK_ATTEMPTS` tries, so a reset never spins forever."""
    attempts: list[int] = []

    async def truncate_once(_engine: Any) -> list[str]:
        attempts.append(1)
        raise _deadlock()

    async def pause(_seconds: float) -> None:
        return None

    monkeypatch.setattr(testing_routes, "_truncate_once", truncate_once)
    monkeypatch.setattr(testing_routes, "_deadlock_pause", pause)

    with pytest.raises(DBAPIError):
        await testing_routes.truncate_tables(OWNER_URL)
    assert len(attempts) == testing_routes.DEADLOCK_ATTEMPTS
    assert testing_routes.DEADLOCK_ATTEMPTS > 5
