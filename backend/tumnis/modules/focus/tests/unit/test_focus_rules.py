"""The focus rules (P2-15, FR-10.1, FR-10.2, FR-10.4, FR-10.7, FR-10.9, FR-11.4): which events
fire at which level, the planned events of a day, the check-in cadence with its back-off,
activity suppression, the today override and the Noul gate. Pure functions on a fixed
clock; the rules are imported inside each test, so this file collects before they exist.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

LEVELS = ("quiet", "nudge", "coach", "guardrail")
KINDS = ("block_start", "not_started", "check_in_due", "switched", "stuck", "block_end", "day_end")
# The plan's level by event table: the levels at which each event fires.
TABLE: dict[str, set[str]] = {
    "block_start": {"nudge", "coach", "guardrail"},
    "not_started": {"nudge", "coach", "guardrail"},
    "check_in_due": {"coach", "guardrail"},
    "switched": {"coach", "guardrail"},
    "stuck": {"coach", "guardrail"},
    "block_end": {"guardrail"},
    "day_end": {"nudge", "coach", "guardrail"},
}
TASK = UUID("0199aa00-0000-7000-8000-00000000f0c5")
OTHER = UUID("0199aa00-0000-7000-8000-00000000f0c6")
START = datetime(2026, 3, 10, 14, 0, tzinfo=UTC)  # 10:00 in New York (EDT)


def _at(hhmm: str) -> datetime:
    """`hh:mm` New York time on Tuesday 2026-03-10 (UTC-4)."""
    hours, minutes = (int(x) for x in hhmm.split(":"))
    return START + timedelta(hours=hours - 10, minutes=minutes)


def _session(base: int = 25) -> Any:
    from tumnis.modules.focus.rules import SessionState  # noqa: PLC0415

    return SessionState(
        task_id=TASK,
        started_at=START,
        base_cadence_min=base,
        streak=0,
        doubled=False,
        last_check_at=START,
        snoozed_until=None,
    )


def _simulate(
    answers: dict[str, str],
    *,
    activity: Sequence[datetime] = (),
    until: str = "12:31",
    base: int = 25,
) -> list[str]:
    """Check-in times (hh:mm) of a Coach session started at 10:00, answered as `answers`
    says (fire time -> response, given at the fire time), with this activity on the task."""
    from tumnis.modules.focus.rules import (  # noqa: PLC0415
        apply_response,
        check_in_fired,
        next_check_in,
        suppressed_by_activity,
    )

    state, end = _session(base), _at(until)
    fired: list[str] = []
    while True:
        due = next_check_in(state, "coach")
        assert due is not None
        if due > end:
            return fired
        if suppressed_by_activity(activity, state.last_check_at, due, signals_on=True):
            state = check_in_fired(state, due)  # a suppressed check moves the window on
            continue
        state = check_in_fired(state, due)
        label = due.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M")
        fired.append(label)
        if label in answers:
            state = apply_response(state, answers[label], due)  # type: ignore[arg-type]


@pytest.mark.req("FR-10.2")
@pytest.mark.wp("P2-15")
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("level", LEVELS)
def test_level_event_table(level: str, kind: str) -> None:
    """T-P2-15-01
    Every cell of the level by event table: Quiet fires nothing; Nudge fires block_start,
    not_started and day_end; Coach adds check_in_due, switched and stuck; Guardrail adds
    block_end.
    """
    from tumnis.modules.focus.rules import LEVEL_EVENTS, fires  # noqa: PLC0415

    assert fires(level, kind) is (level in TABLE[kind])  # type: ignore[arg-type]
    assert (kind in LEVEL_EVENTS[level]) is (level in TABLE[kind])  # type: ignore[index]


@pytest.mark.req("FR-10.2")
@pytest.mark.wp("P2-15")
def test_not_started_at_15_minutes() -> None:
    """T-P2-15-02
    A planned block gives block_start at its start, not_started 15 minutes later and
    block_end at its end; the day gives one day_end at the end of working hours; blocks
    without a time give nothing. not_started holds only while the task is not In progress.
    """
    from tumnis.modules.focus.rules import (  # noqa: PLC0415
        NOT_STARTED_AFTER,
        PlanItemView,
        not_started_holds,
        plan_events,
    )

    assert timedelta(minutes=15) == NOT_STARTED_AFTER
    day_end = _at("18:00")
    items = [
        PlanItemView(task_id=OTHER, block_start=_at("13:00"), block_end=_at("13:30")),
        PlanItemView(task_id=TASK, block_start=_at("10:00"), block_end=_at("10:50")),
        PlanItemView(task_id=UUID(int=7), block_start=None, block_end=None),
    ]
    got = [(e.kind, e.at, e.task_id) for e in plan_events(items, day_end)]
    assert got == [
        ("block_start", _at("10:00"), TASK),
        ("not_started", _at("10:15"), TASK),
        ("block_end", _at("10:50"), TASK),
        ("block_start", _at("13:00"), OTHER),
        ("not_started", _at("13:15"), OTHER),
        ("block_end", _at("13:30"), OTHER),
        ("day_end", _at("18:00"), None),
    ]
    assert plan_events([], day_end)[0].kind == "day_end"
    for status in ("backlog", "today", "waiting_on_human", "in_review", "done"):
        assert not_started_holds(status) is True
    assert not_started_holds("in_progress") is False


BACKOFF_ROWS = [
    pytest.param({}, (), ["10:25", "10:50", "11:15", "11:40", "12:05", "12:30"], id="no-answers"),
    pytest.param(
        {"10:25": "still_on_it", "10:50": "still_on_it"},
        (),
        ["10:25", "10:50", "11:40", "12:30"],
        id="two-still-on-it-double",
    ),
    pytest.param(
        {"10:25": "still_on_it", "10:50": "stuck", "11:15": "still_on_it"},
        (),
        ["10:25", "10:50", "11:15", "11:40", "12:05", "12:30"],
        id="stuck-resets-streak",
    ),
    pytest.param(
        {"10:25": "snooze"},
        (),
        ["10:25", "10:40", "11:05", "11:30", "11:55", "12:20"],
        id="snooze",
    ),
    pytest.param(
        {},
        (_at("10:20"),),
        ["10:50", "11:15", "11:40", "12:05", "12:30"],
        id="activity-suppresses",
    ),
]


@pytest.mark.req("FR-10.4")
@pytest.mark.wp("P2-15")
@pytest.mark.parametrize(("answers", "activity", "expected"), BACKOFF_ROWS)
def test_backoff_sequences(
    answers: dict[str, str], activity: tuple[datetime, ...], expected: list[str]
) -> None:
    """T-P2-15-03
    Every row of the plan's back-off table (base 25 minutes, session from 10:00, Coach):
    unanswered check-ins keep the cadence; two still-on-it answers in a row double it for
    the rest of the task; stuck resets the streak; snooze moves the next one 15 minutes
    out; activity suppresses a check-in and the next one is a full cadence after it.
    """
    from tumnis.modules.focus.rules import (  # noqa: PLC0415
        BACKOFF_AFTER,
        DEFAULT_CADENCE_MIN,
        SNOOZE_FOR,
        cadence,
    )

    assert (25, 2, timedelta(minutes=15)) == (DEFAULT_CADENCE_MIN, BACKOFF_AFTER, SNOOZE_FOR)
    assert cadence(_session()) == timedelta(minutes=25)
    assert _simulate(answers, activity=activity) == expected


ANSWER = st.sampled_from(["still_on_it", "switched", "stuck", None])


@pytest.mark.req("FR-10.4")
@pytest.mark.wp("P2-15")
@settings(deadline=None, max_examples=200)
@given(base=st.integers(min_value=5, max_value=90), answers=st.lists(ANSWER, max_size=12))
def test_backoff_property(base: int, answers: list[str | None]) -> None:
    """T-P2-15-04
    Property: once two consecutive still-on-it answers occur, every later interval between
    check-ins equals twice the base until the session ends; before that, every interval is
    the base.
    """
    from tumnis.modules.focus.rules import (  # noqa: PLC0415
        apply_response,
        check_in_fired,
        next_check_in,
    )

    state = _session(base)
    run, doubled_from = 0, None
    previous = state.last_check_at
    for i, answer in enumerate(answers):
        due = next_check_in(state, "coach")
        assert due is not None
        expected = base * (2 if doubled_from is not None else 1)
        assert due - previous == timedelta(minutes=expected), (i, answers)
        state = check_in_fired(state, due)
        previous = due
        if answer is not None:
            state = apply_response(state, answer, due)  # type: ignore[arg-type]
            run = run + 1 if answer == "still_on_it" else 0
        if run >= 2 and doubled_from is None:
            doubled_from = i
        assert state.doubled is (doubled_from is not None)


@pytest.mark.req("FR-10.7")
@pytest.mark.wp("P2-15")
def test_activity_suppresses_check_in() -> None:
    """T-P2-15-05
    Git or agent activity on the task inside [window start, now] suppresses the check-in
    (both ends included); activity before the window or after now does not; with the
    signals setting off nothing suppresses. A suppressed check moves the window on, so the
    next check-in is a full cadence later.
    """
    from tumnis.modules.focus.rules import (  # noqa: PLC0415
        check_in_fired,
        next_check_in,
        suppressed_by_activity,
    )

    start, now = _at("10:00"), _at("10:25")
    inside = [_at("10:20")]
    assert suppressed_by_activity(inside, start, now, signals_on=True) is True
    assert suppressed_by_activity([start], start, now, signals_on=True) is True
    assert suppressed_by_activity([now], start, now, signals_on=True) is True
    assert suppressed_by_activity([_at("09:59")], start, now, signals_on=True) is False
    assert suppressed_by_activity([_at("10:26")], start, now, signals_on=True) is False
    assert suppressed_by_activity([], start, now, signals_on=True) is False
    assert suppressed_by_activity(inside, start, now, signals_on=False) is False

    moved = check_in_fired(_session(), now)
    assert moved.last_check_at == now
    assert next_check_in(moved, "coach") == _at("10:50")


def _close_after(day: date, tz: ZoneInfo) -> datetime:
    """The day close after local `day`: the next local midnight (P0-19), in UTC."""
    return datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz).astimezone(UTC)


# (zone, a day DST starts, a day DST ends), 2026.
DST = {
    "America/New_York": (date(2026, 3, 8), date(2026, 11, 1)),
    "Australia/Sydney": (date(2026, 10, 4), date(2026, 4, 5)),
}


@pytest.mark.req("FR-10.1")
@pytest.mark.wp("P2-15")
@pytest.mark.parametrize("zone", ["America/New_York", "Australia/Sydney"])
def test_today_override_ends_at_day_close(zone: str) -> None:
    """T-P2-15-06
    The today override applies on its local date until that day's close, not after, on
    both DST change days of the zone; a day without an override, or another day's
    override, leaves the workspace level. "Less of this" lowers one level at a time and
    stops at Quiet.
    """
    from tumnis.modules.focus.rules import OverrideView, effective_level, lower  # noqa: PLC0415

    tz = ZoneInfo(zone)
    for day in DST[zone]:
        override = OverrideView(day=day, level="nudge")
        close = _close_after(day, tz)
        morning = datetime.combine(day, time(9, 0), tzinfo=tz).astimezone(UTC)
        late = close - timedelta(minutes=1)
        assert effective_level("coach", override, morning, tz, close) == "nudge"
        assert effective_level("coach", override, late, tz, close) == "nudge"
        assert effective_level("coach", override, close, tz, close) == "coach"
        next_morning = morning + timedelta(days=1)
        assert effective_level("coach", override, next_morning, tz, close) == "coach"
        assert effective_level("coach", None, morning, tz, close) == "coach"
        before = datetime.combine(day - timedelta(days=1), time(23, 0), tzinfo=tz)
        assert effective_level("coach", override, before.astimezone(UTC), tz, close) == "coach"
    assert [lower(x) for x in LEVELS] == ["quiet", "quiet", "nudge", "coach"]  # type: ignore[arg-type]


@pytest.mark.req("FR-11.4")
@pytest.mark.wp("P2-15")
def test_gate_can_only_suppress_gateable_kinds() -> None:
    """T-P2-15-07
    The Noul ("is a nudge warranted now?") may only suppress, and only not_started and
    check_in_due: a confident "no" at or above the threshold suppresses; a "yes", a low
    confidence, no answer (Decisions down) or no threshold leaves the deterministic rule
    (fire). Every other kind fires whatever the Noul says.
    """
    from tumnis.modules.focus.rules import GATEABLE, NoulAnswer, gate  # noqa: PLC0415

    assert frozenset({"not_started", "check_in_due"}) == GATEABLE
    no = NoulAnswer(p=0.1, confidence=0.8)
    yes = NoulAnswer(p=0.9, confidence=0.8)
    unsure = NoulAnswer(p=0.3, confidence=0.4)
    for kind in ("not_started", "check_in_due"):
        assert gate(kind, no, 0.6) == "suppress"
        assert gate(kind, NoulAnswer(p=0.2, confidence=0.6), 0.6) == "suppress"
        assert gate(kind, yes, 0.6) == "fire"
        assert gate(kind, unsure, 0.6) == "fire"  # below the threshold
        assert gate(kind, None, 0.6) == "fire"  # Decisions down
        assert gate(kind, no, None) == "fire"  # no threshold in force
    for kind in ("block_start", "switched", "stuck", "block_end", "day_end"):
        assert gate(kind, no, 0.6) == "fire"  # type: ignore[arg-type]
