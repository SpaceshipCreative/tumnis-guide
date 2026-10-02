"""The Google Calendar fake's stored scenarios (R-37, `POST /v1/test/fakes/calendar.google/
script`, A1.3): a journey names a scenario, the route stores its events.list answers per
calendar, and the fake of every process answers them instead of the recordings."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from tumnis.core import fake_scripts
from tumnis.modules.calendar.adapters.fake import (
    NAME,
    FakeGoogleCalendar,
    parse_calendar_script,
)

T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
AVERY = "avery@example.com"
BLAKE = "blake@example.org"


@pytest.mark.req("REL-7")
@pytest.mark.wp("P1-09")
def test_scenario_script_stores_each_calendars_answer() -> None:
    """A scenario name is the whole body; the stored script holds one events.list answer
    per calendar of the scenario, under the one key."""
    key, stored = parse_calendar_script({"scenario": "no_ninety_minute_gap"})
    assert key == ""
    assert stored["scenario"] == "no_ninety_minute_gap"
    assert set(stored["calendars"]) == {AVERY, BLAKE}
    for answer in stored["calendars"].values():
        assert answer["kind"] == "calendar#events"
        assert "nextPageToken" not in answer


@pytest.mark.req("REL-7")
@pytest.mark.wp("P1-09")
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"scenario": "no_such_scenario"},
        {"scenario": "../pages/account_a_page1"},
        {"scenario": "no_ninety_minute_gap", "extra": 1},
        {"scenario": 3},
    ],
)
def test_scenario_script_refuses_what_the_fake_cannot_play(body: dict[str, Any]) -> None:
    """An unknown or malformed scenario is refused when posted, not when the fake syncs."""
    with pytest.raises(ValueError, match=r"."):
        parse_calendar_script(body)


async def _list(fake: FakeGoogleCalendar, calendar_id: str) -> dict[str, Any]:
    return await fake.list_events(
        "fake-access-a", calendar_id, time_min=T0, time_max=T0, page_token=None
    )


@pytest.mark.req("REL-7")
@pytest.mark.wp("P1-09")
async def test_fake_answers_the_stored_scenario(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a scenario stored, events.list answers the scenario's calendar (a fresh copy
    each time); with nothing stored it answers the recording again."""
    _, stored = parse_calendar_script({"scenario": "no_ninety_minute_gap"})
    asked: list[str] = []

    async def lookup(name: str, key: str = "") -> dict[str, Any] | None:
        asked.append(name)
        return stored

    monkeypatch.setattr(fake_scripts, "lookup", lookup)
    fake = FakeGoogleCalendar()

    answer = await _list(fake, AVERY)
    assert answer == stored["calendars"][AVERY]
    assert asked == [NAME]
    answer["items"].clear()  # the caller's copy: the next answer is whole again
    assert (await _list(fake, AVERY))["items"] == stored["calendars"][AVERY]["items"]

    monkeypatch.setattr(fake_scripts, "lookup", lambda name, key="": _none())
    recorded = await _list(fake, AVERY)
    assert recorded["nextPageToken"] == "a-p2"


async def _none() -> None:
    return None
