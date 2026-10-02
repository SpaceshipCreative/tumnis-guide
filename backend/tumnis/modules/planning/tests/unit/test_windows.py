"""Unattended run windows (P4-04, FR-4.5, REL-6): a window is a set of weekdays and two
local wall times in the workspace timezone; a window whose end is before its start crosses
midnight and belongs to the weekday it starts on. Bounds come from local wall time, so a
22:00 to 06:00 window is 7 or 9 hours long on the nights the clocks change."""

import importlib
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

NEW_YORK = ZoneInfo("America/New_York")
SYDNEY = ZoneInfo("Australia/Sydney")
WEEKDAYS = frozenset({0, 1, 2, 3, 4})  # Monday to Friday
EVERY_DAY = frozenset(range(7))


def _rules() -> Any:
    return importlib.import_module("tumnis.modules.planning.rules")


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def _local(text: str, tz: ZoneInfo) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=tz).astimezone(UTC)


# (weekdays, start, end, now (UTC), zone, expected bounds (UTC) or None)
CASES: dict[str, tuple[frozenset[int], time, time, datetime, ZoneInfo, tuple[str, str] | None]] = {
    # Sunday 2026-03-08 (spring forward in New York): Sunday is not in the weekdays, and
    # Saturday's night does not run either.
    "ny_spring_forward_sunday_outside": (
        WEEKDAYS,
        time(22),
        time(6),
        _local("2026-03-08T23:00:00", NEW_YORK),
        NEW_YORK,
        None,
    ),
    # Monday 2026-03-09 23:00 EDT: inside Monday's night, 22:00 EDT to Tuesday 06:00 EDT.
    "ny_monday_night_inside": (
        WEEKDAYS,
        time(22),
        time(6),
        _local("2026-03-09T23:00:00", NEW_YORK),
        NEW_YORK,
        ("2026-03-10T02:00:00", "2026-03-10T10:00:00"),
    ),
    # Tuesday 05:59 local still belongs to Monday's night.
    "ny_tuesday_early_inside": (
        WEEKDAYS,
        time(22),
        time(6),
        _local("2026-03-10T05:59:00", NEW_YORK),
        NEW_YORK,
        ("2026-03-10T02:00:00", "2026-03-10T10:00:00"),
    ),
    # Tuesday 06:00 local: the window has ended (the end is exclusive).
    "ny_tuesday_end_outside": (
        WEEKDAYS,
        time(22),
        time(6),
        _local("2026-03-10T06:00:00", NEW_YORK),
        NEW_YORK,
        None,
    ),
    # Monday 21:59 local: not open yet.
    "ny_before_start_outside": (
        WEEKDAYS,
        time(22),
        time(6),
        _local("2026-03-09T21:59:00", NEW_YORK),
        NEW_YORK,
        None,
    ),
    # Sunday 2026-11-01 (fall back in New York): outside, Sunday is not in the weekdays.
    "ny_fall_back_sunday_outside": (
        WEEKDAYS,
        time(22),
        time(6),
        _local("2026-11-01T23:00:00", NEW_YORK),
        NEW_YORK,
        None,
    ),
    # Friday 2026-10-30's window ends Saturday 06:00: Saturday 03:00 is inside it.
    "ny_friday_night_into_saturday_inside": (
        WEEKDAYS,
        time(22),
        time(6),
        _local("2026-10-31T03:00:00", NEW_YORK),
        NEW_YORK,
        ("2026-10-31T02:00:00", "2026-10-31T10:00:00"),
    ),
    # Saturday 2026-03-07's night in New York, every day: 22:00 EST to Sunday 06:00 EDT,
    # 7 hours (the night the clocks go forward).
    "ny_spring_forward_night_7h": (
        EVERY_DAY,
        time(22),
        time(6),
        _local("2026-03-08T04:00:00", NEW_YORK),
        NEW_YORK,
        ("2026-03-08T03:00:00", "2026-03-08T10:00:00"),
    ),
    # Saturday 2026-10-31's night, every day: 22:00 EDT to Sunday 06:00 EST, 9 hours.
    "ny_fall_back_night_9h": (
        EVERY_DAY,
        time(22),
        time(6),
        _local("2026-11-01T01:30:00", NEW_YORK),
        NEW_YORK,
        ("2026-11-01T02:00:00", "2026-11-01T11:00:00"),
    ),
    # A same-day window (Monday to Friday 09:00 to 17:00), Monday 12:00 local.
    "ny_same_day_inside": (
        WEEKDAYS,
        time(9),
        time(17),
        _local("2026-03-09T12:00:00", NEW_YORK),
        NEW_YORK,
        ("2026-03-09T13:00:00", "2026-03-09T21:00:00"),
    ),
    "ny_same_day_after_outside": (
        WEEKDAYS,
        time(9),
        time(17),
        _local("2026-03-09T17:30:00", NEW_YORK),
        NEW_YORK,
        None,
    ),
    # Sydney, Saturday 2026-04-04's night (clocks go back Sunday 03:00 AEDT -> 02:00 AEST):
    # 22:00 AEDT (11:00Z) to Sunday 06:00 AEST (20:00Z), 9 hours.
    "sydney_autumn_night_9h": (
        EVERY_DAY,
        time(22),
        time(6),
        _local("2026-04-05T02:30:00", SYDNEY),
        SYDNEY,
        ("2026-04-04T11:00:00", "2026-04-04T20:00:00"),
    ),
    # Sydney, Saturday 2026-10-03's night (clocks go forward Sunday 02:00 AEST -> 03:00
    # AEDT): 22:00 AEST (12:00Z) to Sunday 06:00 AEDT (19:00Z), 7 hours.
    "sydney_spring_night_7h": (
        EVERY_DAY,
        time(22),
        time(6),
        _utc("2026-10-03T18:00:00"),
        SYDNEY,
        ("2026-10-03T12:00:00", "2026-10-03T19:00:00"),
    ),
    # Sydney weekdays only: Sunday 2026-10-04 is outside.
    "sydney_sunday_outside": (
        WEEKDAYS,
        time(22),
        time(6),
        _local("2026-10-04T23:00:00", SYDNEY),
        SYDNEY,
        None,
    ),
}


@pytest.mark.req("FR-4.5", "REL-6")
@pytest.mark.wp("P4-04")
@pytest.mark.parametrize("case", list(CASES))
def test_window_bounds_table(case: str) -> None:
    """T-P4-04-01
    Same-day and crossing windows, weekdays, and both DST nights in America/New_York and
    Australia/Sydney: `window_bounds` gives the instance containing `now` in UTC (or None),
    and `window_open` agrees with it.
    """
    rules = _rules()
    weekdays, start, end, now, tz, expected = CASES[case]
    window = rules.Window(weekdays=weekdays, start_local=start, end_local=end)

    bounds = rules.window_bounds(window, now, tz)

    if expected is None:
        assert bounds is None
        assert rules.window_open(window, now, tz) is False
    else:
        assert bounds == (_utc(expected[0]), _utc(expected[1]))
        assert rules.window_open(window, now, tz) is True


@pytest.mark.req("FR-4.5", "REL-6")
@pytest.mark.wp("P4-04")
def test_window_lengths_follow_wall_time_on_dst_nights() -> None:
    """T-P4-04-01
    The same 22:00 to 06:00 window lasts 7 hours on a spring-forward night and 9 hours on a
    fall-back night, in both zones: bounds come from local wall times, never a fixed length.
    """
    rules = _rules()
    window = rules.Window(weekdays=EVERY_DAY, start_local=time(22), end_local=time(6))
    for case, hours in (
        ("ny_spring_forward_night_7h", 7),
        ("ny_fall_back_night_9h", 9),
        ("sydney_autumn_night_9h", 9),
        ("sydney_spring_night_7h", 7),
    ):
        _, _, _, now, tz, _ = CASES[case]
        bounds = rules.window_bounds(window, now, tz)
        assert bounds is not None
        start, end = bounds
        assert end - start == timedelta(hours=hours), case


@pytest.mark.req("REL-6")
@pytest.mark.wp("P4-04")
def test_timezone_change_moves_window() -> None:
    """T-P4-04-02
    The same window (Monday to Friday, 22:00 to 06:00) read in another zone keeps its wall
    times and moves its UTC bounds: Monday night in New York starts 02:00Z, in Sydney the
    Monday night starts 11:00Z the same calendar Monday (22:00 AEDT).
    """
    rules = _rules()
    window = rules.Window(weekdays=WEEKDAYS, start_local=time(22), end_local=time(6))

    ny = rules.window_bounds(window, _local("2026-03-09T23:00:00", NEW_YORK), NEW_YORK)
    sydney = rules.window_bounds(window, _local("2026-03-09T23:00:00", SYDNEY), SYDNEY)

    assert ny == (_utc("2026-03-10T02:00:00"), _utc("2026-03-10T10:00:00"))
    assert sydney == (_utc("2026-03-09T11:00:00"), _utc("2026-03-09T19:00:00"))
    assert ny is not None
    assert sydney is not None
    for (start, end), tz in ((ny, NEW_YORK), (sydney, SYDNEY)):
        assert start.astimezone(tz).time() == time(22)
        assert end.astimezone(tz).time() == time(6)
    # The instant that is inside New York's window is outside Sydney's (Tuesday 14:00 AEDT).
    instant = _local("2026-03-09T23:00:00", NEW_YORK)
    assert rules.window_open(window, instant, NEW_YORK) is True
    assert rules.window_open(window, instant, SYDNEY) is False
