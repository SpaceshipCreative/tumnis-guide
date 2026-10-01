"""Edges of the plan rules the spec tables leave out (P1-11): `check_picks` code by code,
a Human task with no estimate (validated, placed and offered), and `free_left`."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from tumnis.core.types import Interval
from tumnis.modules.planning import rules

NEW_YORK = ZoneInfo("America/New_York")
DAY = date(2026, 3, 9)
PROJECT = UUID("0199aa00-0000-7000-8000-000000000001")


def at(hhmm: str) -> datetime:
    return datetime.combine(DAY, time.fromisoformat(hhmm), NEW_YORK).astimezone(UTC)


def span(start: str, end: str) -> Interval:
    return Interval(at(start), at(end))


def task(
    n: int, *, label: str = "human", estimate: int | None = 60, blocked: bool = False
) -> rules.PlanTask:
    return rules.PlanTask(
        task_id=UUID(f"0199aa00-0000-7000-8000-{n:012x}"),
        project_id=PROJECT,
        label=label,
        estimate_minutes=estimate,
        status="backlog",
        blocked=blocked,
        due_on=None,
        priority=1,
        rollover_count=0,
        created_at=at("07:00") - timedelta(days=n),
        eligible=True,
    )


def context(*tasks: rules.PlanTask) -> rules.PlanContext:
    return rules.PlanContext(
        day=DAY,
        tz="America/New_York",
        now=at("08:30"),
        free_blocks=[span("10:00", "12:00")],
        tasks={t.task_id: t for t in tasks},
        replan=False,
    )


ONE, TWO, BLOCKED = task(1), task(2), task(3, blocked=True)
CTX = context(ONE, TWO, BLOCKED)
UNKNOWN = UUID("0199aa00-0000-7000-8000-0000000003e7")


def codes(found: list[rules.Violation]) -> list[tuple[str, UUID | None]]:
    return [(v.code, v.task_id) for v in found]


def test_check_picks_accepts_a_clean_reply() -> None:
    picks = [rules.PlanPick(task_id=ONE.task_id, reason="Due today")]
    assert rules.check_picks(picks, CTX) == []


@pytest.mark.parametrize(
    ("picks", "expected"),
    [
        pytest.param(
            [(ONE.task_id, "a"), (ONE.task_id, "b")],
            [("duplicate_task", ONE.task_id)],
            id="duplicate",
        ),
        pytest.param([(UNKNOWN, "a")], [("unknown_task", UNKNOWN)], id="unknown"),
        pytest.param(
            [(BLOCKED.task_id, "a")], [("ineligible_task", BLOCKED.task_id)], id="blocked"
        ),
        pytest.param([(ONE.task_id, "  ")], [("missing_reason", ONE.task_id)], id="blank"),
        pytest.param(
            [(ONE.task_id, "x" * (rules.MAX_REASON + 1))],
            [("reason_too_long", ONE.task_id)],
            id="long_reason",
        ),
        pytest.param(
            [(ONE.task_id, "a")] * (rules.MAX_ITEMS + 1),
            [("too_many_items", None)] + [("duplicate_task", ONE.task_id)] * rules.MAX_ITEMS,
            id="six",
        ),
    ],
)
def test_check_picks_names_each_violation(
    picks: list[tuple[UUID, str]], expected: list[tuple[str, UUID | None]]
) -> None:
    reply = [rules.PlanPick(task_id=tid, reason=reason) for tid, reason in picks]
    assert codes(rules.check_picks(reply, CTX)) == expected


def test_human_task_without_an_estimate() -> None:
    no_estimate = task(4, estimate=None)
    ctx = context(no_estimate)
    item = rules.PlannedItem(task_id=no_estimate.task_id, position=1, reason="Why", block=None)
    assert codes(rules.validate_plan([item], ctx)) == [("missing_estimate", no_estimate.task_id)]

    pick = rules.PlanPick(task_id=no_estimate.task_id, reason="Why")
    items, unplaceable = rules.assign_blocks([pick], ctx)
    assert items == []
    assert unplaceable == [
        rules.Unplaceable(
            task_id=no_estimate.task_id,
            reason="No estimate yet",
            offer=rules.FitOffer(split=None, move_to=None),
        )
    ]
    assert rules.fit_offer(no_estimate, [span("10:00", "12:00")], {}) == rules.FitOffer(
        split=None, move_to=None
    )


@pytest.mark.parametrize(
    ("free", "taken", "left"),
    [
        pytest.param([("10:00", "12:00")], [], [("10:00", "12:00")], id="nothing_taken"),
        pytest.param(
            [("10:00", "12:00")],
            [("10:30", "11:00")],
            [("10:00", "10:30"), ("11:00", "12:00")],
            id="middle",
        ),
        pytest.param(
            [("13:00", "15:00"), ("10:00", "12:00")],
            [("10:00", "11:00"), ("14:00", "15:00"), ("16:00", "17:00")],
            [("11:00", "12:00"), ("13:00", "14:00")],
            id="edges_and_outside",
        ),
        pytest.param([("10:00", "11:00")], [("09:00", "12:00")], [], id="covered"),
    ],
)
def test_free_left_cuts_the_taken_blocks(
    free: list[tuple[str, str]], taken: list[tuple[str, str]], left: list[tuple[str, str]]
) -> None:
    result = rules.free_left([span(*f) for f in free], [span(*t) for t in taken])
    assert result == [span(*b) for b in left]


def test_fallback_plan_stops_at_max_items() -> None:
    """More small tasks fit than a plan may hold: the due-date plan places the first
    MAX_ITEMS in fallback order and stops (a fixed case, so coverage never rests on the
    property test's draws)."""
    small = [task(n, estimate=15) for n in range(1, rules.MAX_ITEMS + 3)]
    items, unplaceable = rules.fallback_plan(context(*small))
    oldest_first = sorted(small, key=lambda t: t.created_at)[: rules.MAX_ITEMS]
    assert [i.task_id for i in items] == [t.task_id for t in oldest_first]
    assert unplaceable == []
