"""The working window of a day in the workspace timezone (P1-10, FR-4.7, REL-6).

Working hours are local wall times per weekday (0 = Monday); the window is their UTC
instants on the day, through the R-12 conversion (a time in a DST gap moves forward by the
gap, an ambiguous one takes its first occurrence). The DST days come from
`tests/fixtures/dst_days.yaml`. The rules are imported inside each test, so this file
collects before they exist.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml

DST_DAYS = Path(__file__).resolve().parents[5] / "tests" / "fixtures" / "dst_days.yaml"
NEW_YORK = ZoneInfo("America/New_York")
WEEKDAYS = {day: (time(9, 0), time(18, 0)) for day in range(5)}  # Monday to Friday


def _dst_days() -> list[dict[str, Any]]:
    days: list[dict[str, Any]] = yaml.safe_load(DST_DAYS.read_text())
    return days


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


@pytest.mark.req("FR-4.7")
@pytest.mark.wp("P1-10")
@pytest.mark.xfail(strict=True, reason="spec:P1-10")
def test_weekend_has_no_window_unless_replan() -> None:
    """T-P1-10-06
    Saturday and Sunday have no window with Monday-to-Friday hours; with `replan=True`
    they get the default 09:00 to 18:00 local (FR-4.7).
    """
    from tumnis.modules.planning.rules import DEFAULT_HOURS, working_window  # noqa: PLC0415

    assert (time(9, 0), time(18, 0)) == DEFAULT_HOURS
    saturday, sunday = date(2026, 3, 14), date(2026, 3, 15)
    for day in (saturday, sunday):
        assert working_window(day, NEW_YORK, WEEKDAYS, replan=False) is None
        window = working_window(day, NEW_YORK, WEEKDAYS, replan=True)
        assert window is not None
        assert window.start == datetime.combine(day, time(13, 0), UTC)  # 09:00 EDT
        assert window.end == datetime.combine(day, time(22, 0), UTC)  # 18:00 EDT
        assert window.start.astimezone(NEW_YORK).time() == time(9, 0)
        assert window.end.astimezone(NEW_YORK).time() == time(18, 0)


# Ordinary Sundays beside the changes, where 01:00 to 04:00 is three hours.
PLAIN_DAYS = [("America/New_York", date(2026, 3, 15)), ("Australia/Sydney", date(2026, 10, 11))]
DST_CASES = [
    pytest.param(case["zone"], case["date"], case["direction"], id=f"{case['zone']}-{case['date']}")
    for case in _dst_days()
] + [pytest.param(zone, day, "none", id=f"{zone}-{day}") for zone, day in PLAIN_DAYS]


@pytest.mark.req("REL-6")
@pytest.mark.wp("P1-10")
@pytest.mark.xfail(strict=True, reason="spec:P1-10")
@pytest.mark.parametrize(("zone", "day", "direction"), DST_CASES)
def test_dst_days(zone: str, day: date, direction: str) -> None:
    """T-P1-10-07
    New York 2026-03-08 and 2026-11-01, Sydney 2026-10-04 and 2026-04-05 (all Sundays),
    with `replan=True` and custom hours 01:00 to 04:00 so the window crosses the change:
    2 hours on a spring-forward day, 4 on a fall-back day, 3 on an ordinary one.
    """
    from tumnis.modules.planning.rules import working_window  # noqa: PLC0415

    tz = ZoneInfo(zone)
    hours = {day.weekday(): (time(1, 0), time(4, 0))}
    window = working_window(day, tz, hours, replan=True)
    assert window is not None
    expected = {"forward": 2, "back": 4, "none": 3}[direction]
    assert window.end - window.start == timedelta(hours=expected)
    assert window.minutes == expected * 60
    assert window.start == datetime.combine(day, time(1, 0), tz).astimezone(UTC)
    assert window.start.astimezone(tz).time() == time(1, 0)
    assert window.end.astimezone(tz).time() == time(4, 0)


@pytest.mark.req("REL-6")
@pytest.mark.wp("P1-10")
@pytest.mark.xfail(strict=True, reason="spec:P1-10")
def test_local_to_utc_gap_and_overlap() -> None:
    """T-P1-10-08
    Through `core.clock.local_to_utc` (R-12), and the same conversion the planning rules use:
    02:30 on 2026-03-08 in New York moves forward by the one-hour gap to 03:30 EDT, 07:30
    UTC; 01:30 on 2026-11-01 takes its first occurrence (EDT), 05:30 UTC. A working window
    starting at those times starts at the same instants.
    """
    from tumnis.core.clock import local_to_utc  # noqa: PLC0415
    from tumnis.modules.planning import rules  # noqa: PLC0415

    [spring] = [c for c in _dst_days() if c["zone"] == NEW_YORK.key and c["direction"] == "forward"]
    [fall] = [c for c in _dst_days() if c["zone"] == NEW_YORK.key and c["direction"] == "back"]
    gap = local_to_utc(spring["date"], time(2, 30), NEW_YORK)
    overlap = local_to_utc(fall["date"], time(1, 30), NEW_YORK)

    assert gap == _utc("2026-03-08T07:30:00Z")
    assert gap.astimezone(NEW_YORK).time() == time(3, 30)
    assert overlap == _utc("2026-11-01T05:30:00Z")
    assert overlap.astimezone(NEW_YORK).utcoffset() == timedelta(hours=-4)  # EDT, first

    assert rules.local_to_utc(spring["date"], time(2, 30), NEW_YORK) == gap
    assert rules.local_to_utc(fall["date"], time(1, 30), NEW_YORK) == overlap
    for case, start in ((spring, time(2, 30)), (fall, time(1, 30))):
        hours = {case["date"].weekday(): (start, time(5, 0))}
        window = rules.working_window(case["date"], NEW_YORK, hours, replan=True)
        assert window is not None
        assert window.start == local_to_utc(case["date"], start, NEW_YORK)
    # The transitions in the fixture are where the offsets change.
    for case in (spring, fall):
        instant = _utc(case["transition"])
        before = (instant - timedelta(minutes=1)).astimezone(NEW_YORK).utcoffset()
        assert instant.astimezone(NEW_YORK).utcoffset() != before


@pytest.mark.req("REL-6")
@pytest.mark.wp("P1-10")
@pytest.mark.xfail(strict=True, reason="spec:P1-10")
def test_monday_after_dst_keeps_local_hours() -> None:
    """T-P1-10-09
    The window stays 09:00 to 18:00 local across the US spring change: Monday 2026-03-09
    (EDT) is 13:00 to 22:00 UTC; Friday 2026-03-06 (EST) was 14:00 to 23:00 UTC.
    """
    from tumnis.modules.planning.rules import working_window  # noqa: PLC0415

    monday = working_window(date(2026, 3, 9), NEW_YORK, WEEKDAYS, replan=False)
    friday = working_window(date(2026, 3, 6), NEW_YORK, WEEKDAYS, replan=False)
    assert monday is not None
    assert friday is not None
    assert (monday.start, monday.end) == (
        _utc("2026-03-09T13:00:00Z"),
        _utc("2026-03-09T22:00:00Z"),
    )
    assert (friday.start, friday.end) == (
        _utc("2026-03-06T14:00:00Z"),
        _utc("2026-03-06T23:00:00Z"),
    )
