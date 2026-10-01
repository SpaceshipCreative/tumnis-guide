"""When the morning plan is due (P1-11, FR-4.3, FR-4.7, REL-6): `planner_tick` runs every
5 minutes in UTC and asks `is_plan_due` per workspace in its own zone, so a DST change
never fires a plan twice or skips one. The DST days come from
`tests/fixtures/dst_days.yaml` (shared with P0-19 and P1-10)."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml

from tumnis.modules.planning import rules

DST_DAYS = Path(__file__).resolve().parents[5] / "tests" / "fixtures" / "dst_days.yaml"
TICK = timedelta(minutes=5)
SPAN = timedelta(hours=36)
ALL_WEEKDAYS = frozenset(range(7))
PLAN_TIMES = [time(8, 30), time(2, 30), time(1, 30)]


def _cases() -> list[Any]:
    days: list[dict[str, Any]] = yaml.safe_load(DST_DAYS.read_text())
    return [
        pytest.param(
            case["zone"],
            case["date"],
            case["direction"],
            datetime.fromisoformat(case["transition"]).astimezone(UTC),
            plan_time,
            id=f"{case['zone']}-{case['direction']}-{plan_time.strftime('%H%M')}",
        )
        for case in days
        for plan_time in PLAN_TIMES
    ]


def _ticks(start: datetime, end: datetime) -> list[datetime]:
    out, at = [], start
    while at < end:
        out.append(at)
        at += TICK
    return out


def _run(
    ticks: list[datetime], tz: ZoneInfo, plan_time: time, weekdays: frozenset[int]
) -> list[tuple[datetime, date]]:
    """Every tick asks once; a due answer builds the plan, so later ticks of that local day
    see `already_built`."""
    built: set[date] = set()
    due: list[tuple[datetime, date]] = []
    for now in ticks:
        local_day = now.astimezone(tz).date()
        answer = rules.is_plan_due(now, tz, plan_time, weekdays, local_day in built)
        if answer is not None:
            due.append((now, answer))
            built.add(answer)
    return due


def _local_to_utc(day: date, local: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, local, tzinfo=tz).astimezone(UTC)


@pytest.mark.req("FR-4.3", "REL-6")
@pytest.mark.wp("P1-11")
@pytest.mark.xfail(strict=True, reason="spec:P1-11")
@pytest.mark.parametrize(("zone", "day", "direction", "transition", "plan_time"), _cases())
def test_dst_fires_once_per_local_day(
    zone: str, day: date, direction: str, transition: datetime, plan_time: time
) -> None:
    """T-P1-11-06
    Ticks every 5 minutes from 36 h before to 36 h after the change, all weekdays enabled:
    exactly one due result per local day whose plan time falls inside the run, never two,
    always that tick's local day; on the spring-forward day a 02:30 plan time first fires at
    03:30 local (R-12).
    """
    tz = ZoneInfo(zone)
    start, end = transition - SPAN, transition + SPAN
    due = _run(_ticks(start, end), tz, plan_time, ALL_WEEKDAYS)

    per_day = Counter(answer for _, answer in due)
    assert all(count == 1 for count in per_day.values()), per_day
    assert all(now.astimezone(tz).date() == answer for now, answer in due)
    first, last = start.astimezone(tz).date(), end.astimezone(tz).date()
    expected = {
        d
        for n in range((last - first).days + 1)
        if start <= _local_to_utc(d := first + timedelta(days=n), plan_time, tz) < end
    }
    assert expected <= set(per_day)
    if direction == "forward" and plan_time == time(2, 30):
        [fired] = [now for now, answer in due if answer == day]
        assert fired.astimezone(tz).time() == time(3, 30)


@pytest.mark.req("FR-4.7")
@pytest.mark.wp("P1-11")
@pytest.mark.xfail(strict=True, reason="spec:P1-11")
def test_weekend_no_morning_plan() -> None:
    """T-P1-11-07
    Default plan weekdays (Monday to Friday) and plan time 08:30 in New York, ticks from
    Saturday 2026-03-14 to Monday night: Saturday and Sunday are never due; Monday is due
    once, at 08:30 local.
    """
    tz = ZoneInfo("America/New_York")
    start = datetime(2026, 3, 14, tzinfo=tz).astimezone(UTC)
    end = datetime(2026, 3, 17, tzinfo=tz).astimezone(UTC)
    due = _run(_ticks(start, end), tz, rules.DEFAULT_PLAN_TIME, rules.DEFAULT_PLAN_WEEKDAYS)

    assert time(8, 30) == rules.DEFAULT_PLAN_TIME
    assert [answer for _, answer in due] == [date(2026, 3, 16)]
    [(fired, _)] = due
    assert fired.astimezone(tz) == datetime(2026, 3, 16, 8, 30, tzinfo=tz)
