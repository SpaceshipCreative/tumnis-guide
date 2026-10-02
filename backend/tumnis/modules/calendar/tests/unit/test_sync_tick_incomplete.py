"""The `calendar-sync` test tick reports a sync that didn't finish (P1-09 follow-up,
CodeRabbit on #164): a sync still running when TICK_WAIT_S runs out, or one still `busy`
after its retries, answers 503 `sync_incomplete`, so a journey fails at the tick instead of
reading events that never landed."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from tumnis.core.errors import ProblemError
from tumnis.modules.calendar import testing

NOW = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


@pytest.fixture
def one_account(monkeypatch: pytest.MonkeyPatch) -> None:
    async def connections() -> list[tuple[uuid.UUID, uuid.UUID]]:
        return [(uuid.uuid4(), uuid.uuid4())]

    monkeypatch.setattr(testing, "_connections", connections)


@pytest.mark.req("REL-7")
@pytest.mark.wp("P1-09")
async def test_tick_timeout_answers_sync_incomplete(
    one_account: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sync that outlasts TICK_WAIT_S makes the tick raise 503 `sync_incomplete`."""

    async def slow(*_: Any) -> str:
        await asyncio.sleep(10)
        return "synced"

    monkeypatch.setattr(testing, "_sync", slow)
    monkeypatch.setattr(testing, "TICK_WAIT_S", 0.01)
    with pytest.raises(ProblemError) as raised:
        await testing.tick(object(), NOW)
    assert raised.value.problem.status == 503
    assert raised.value.problem.code == "sync_incomplete"


@pytest.mark.req("REL-7")
@pytest.mark.wp("P1-09")
async def test_tick_busy_sync_answers_sync_incomplete(
    one_account: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sync still `busy` after its retries makes the tick raise 503 `sync_incomplete`."""

    async def busy(*_: Any) -> str:
        return "busy"

    monkeypatch.setattr(testing, "_sync", busy)
    with pytest.raises(ProblemError) as raised:
        await testing.tick(object(), NOW)
    assert raised.value.problem.status == 503
    assert raised.value.problem.code == "sync_incomplete"


@pytest.mark.req("REL-7")
@pytest.mark.wp("P1-09")
async def test_tick_counts_finished_syncs(
    one_account: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Syncs that ended answer with the number of accounts synced."""

    async def synced(*_: Any) -> str:
        return "synced"

    monkeypatch.setattr(testing, "_sync", synced)
    assert await testing.tick(object(), NOW) == 1
