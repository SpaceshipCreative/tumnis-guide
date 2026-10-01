"""Local success metrics (P1-18, PRD Success metrics): pure functions over what the tables
already hold, checked on fixture event streams. Nothing here is sent anywhere."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tumnis.modules.usage import rules
from tumnis.modules.usage.tests.streams import all_streams, load

NEW_YORK = ZoneInfo("America/New_York")
WORKING_WEEK = frozenset(range(5))


@pytest.mark.req("Success metrics")
@pytest.mark.wp("P1-18")
@pytest.mark.xfail(strict=True, reason="spec:P1-18")
@pytest.mark.parametrize("fixture", all_streams(), ids=lambda p: Path(p).stem)
def test_stream(fixture: Path) -> None:
    """T-P1-18-03
    Each fixture event stream yields its expected daily open rate, tasks completed per
    working day, rollover rate, estimate error and run of planned days.
    """
    s = load(fixture)
    got = {
        "daily_open_rate": rules.daily_open_rate(s.open_days, s.start, s.end),
        "tasks_completed_per_working_day": rules.tasks_completed_per_working_day(
            s.done_at, s.tz, s.start, s.end, s.weekdays
        ),
        "rollover_rate": rules.rollover_rate(s.planned),
        "estimate_error": rules.estimate_error(s.estimate_pairs),
        "consecutive_plan_days": rules.consecutive_plan_days(s.plan_days, s.end, s.weekdays),
    }
    for name, want in s.expect.items():
        assert got[name] == (want if want is None else pytest.approx(want)), name


def _gate(days: list[tuple[date, bool, bool]]) -> dict[date, rules.PlanDayFacts]:
    return {d: rules.PlanDayFacts(published=p, decided=x) for d, p, x in days}


@pytest.mark.req("Success metrics")
@pytest.mark.wp("P1-18")
@pytest.mark.xfail(strict=True, reason="spec:P1-18")
def test_consecutive_plan_days_exit_gate() -> None:
    """T-P1-18-04
    10 working days with plans and decisions give 10; a day with a plan but no decision
    breaks the run; weekends are skipped.
    """
    start = date(2026, 3, 9)  # Monday
    span = [start + timedelta(days=i) for i in range(12)]  # to Friday 20 March
    weekdays = [d for d in span if d.weekday() in WORKING_WEEK]
    assert len(weekdays) == 10
    end = span[-1]

    planned = _gate([(d, True, True) for d in weekdays])
    assert rules.consecutive_plan_days(planned, end, WORKING_WEEK) == 10

    # Wednesday 18 March has a plan but nobody accepted, swapped or removed an item.
    gap = dict(planned)
    gap[date(2026, 3, 18)] = rules.PlanDayFacts(published=True, decided=False)
    assert rules.consecutive_plan_days(gap, end, WORKING_WEEK) == 2

    # A decision without a published plan does not count either.
    unpublished = dict(planned)
    unpublished[date(2026, 3, 19)] = rules.PlanDayFacts(published=False, decided=True)
    assert rules.consecutive_plan_days(unpublished, end, WORKING_WEEK) == 1

    # A working day with no record at all breaks the run.
    missing = {d: f for d, f in planned.items() if d != date(2026, 3, 16)}
    assert rules.consecutive_plan_days(missing, end, WORKING_WEEK) == 4

    # Ending on a Sunday counts back from Friday; a weekend plan neither adds nor breaks.
    sunday = date(2026, 3, 22)
    assert rules.consecutive_plan_days(planned, sunday, WORKING_WEEK) == 10
    weekend = planned | _gate([(date(2026, 3, 14), True, True)])
    assert rules.consecutive_plan_days(weekend, end, WORKING_WEEK) == 10


@pytest.mark.req("Success metrics")
@pytest.mark.wp("P1-18")
@pytest.mark.xfail(strict=True, reason="spec:P1-18")
def test_metrics_with_no_data_are_none() -> None:
    """T-P1-18-05
    Empty inputs return None, never 0 or a division error.
    """
    monday, friday = date(2026, 3, 9), date(2026, 3, 13)
    assert rules.daily_open_rate(set(), monday, friday) is None
    assert rules.daily_open_rate({monday}, friday, monday) is None  # an empty range
    assert rules.tasks_completed_per_working_day([], NEW_YORK, monday, friday, WORKING_WEEK) is None
    # a completion, but no working day in the range to divide by
    done = [datetime(2026, 3, 14, 16, tzinfo=UTC)]
    saturday, sunday = date(2026, 3, 14), date(2026, 3, 15)
    assert (
        rules.tasks_completed_per_working_day(done, NEW_YORK, saturday, sunday, WORKING_WEEK)
        is None
    )
    assert rules.rollover_rate([]) is None
    assert rules.estimate_error([]) is None
    assert rules.estimate_error([(0, 30)]) is None  # a zero estimate has no ratio
    assert rules.consecutive_plan_days({}, friday, WORKING_WEEK) == 0
