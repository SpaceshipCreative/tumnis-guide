"""The plan's hard constraints and its placement (P1-11, FR-4.3): `validate_plan` row by
row (the table is the spec), and the properties that tie `assign_blocks` and
`fallback_plan` to it.

Table context unless a row says otherwise: Monday 2026-03-09 in New York, `now` 08:30
local, free blocks 10:00 to 12:00 and 13:00 to 15:00 local. Task names say the label and
the estimate: `H60` is a Human task of 60 minutes, `Y30` a Hybrid one of 30, `AI` an AI
task.

The rules are reached through the module (`rules.validate_plan`, ...) and their models
are built inside the tests, so this file collects before the code exists.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tumnis.core.types import Interval
from tumnis.modules.planning import rules

NEW_YORK = ZoneInfo("America/New_York")
DAY = date(2026, 3, 9)
PROJECT = UUID("0199aa00-0000-7000-8000-000000000001")
ZONES = ["America/New_York", "Australia/Sydney", "Europe/London", "UTC"]
PROPERTY = settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])


def at(hhmm: str, day: date = DAY, tz: ZoneInfo = NEW_YORK) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tz).astimezone(UTC)


def span(start: str, end: str) -> Interval:
    return Interval(at(start), at(end))


FREE = [span("10:00", "12:00"), span("13:00", "15:00")]


def task_id(n: int) -> UUID:
    return UUID(f"0199aa00-0000-7000-8000-{n:012x}")


def plan_task(  # every PlanTask field a test varies
    n: int,
    label: str = "human",
    estimate: int | None = 60,
    status: str = "backlog",
    *,
    blocked: bool = False,
    eligible: bool = True,
    due_on: date | None = None,
    priority: int = 1,
    rollover_count: int = 0,
    created_at: datetime | None = None,
) -> rules.PlanTask:
    return rules.PlanTask(
        task_id=task_id(n),
        project_id=PROJECT,
        label=label,
        estimate_minutes=estimate,
        status=status,
        blocked=blocked,
        due_on=due_on,
        priority=priority,
        rollover_count=rollover_count,
        created_at=created_at or at("07:00") - timedelta(days=n),
        eligible=eligible,
    )


# name -> (task number, label, estimate, status, blocked, eligible)
TASKS: dict[str, tuple[int, str, int | None, str, bool, bool]] = {
    "H60": (1, "human", 60, "backlog", False, True),
    "Y30": (2, "hybrid", 30, "backlog", False, True),
    "H90": (3, "human", 90, "backlog", False, True),
    "AI": (4, "ai", None, "backlog", False, True),
    "AI2": (5, "ai", None, "today", False, True),
    "AI3": (6, "ai", None, "backlog", False, True),
    "AI4": (7, "ai", None, "backlog", False, True),
    "H30": (8, "human", 30, "backlog", False, True),
    "DONE": (9, "ai", None, "done", False, False),
    "WAITING": (10, "ai", None, "waiting_on_human", True, True),
    "H_NO_EST": (11, "human", None, "backlog", False, True),
    "Y60": (12, "hybrid", 60, "backlog", False, True),
    "H120": (13, "human", 120, "backlog", False, True),
    "H120B": (14, "human", 120, "backlog", False, True),
}
UNKNOWN = task_id(999)

# (task name, reason, block start, block end); a None start means no block.
Item = tuple[str, str, str | None, str | None]
VALID_FOUR: list[Item] = [
    ("H60", "Due today", "10:00", "11:00"),
    ("Y30", "Client is waiting", "11:00", "11:30"),
    ("H90", "Rolled over twice", "13:00", "14:30"),
    ("AI", "Runs while you work", None, None),
]

ROWS: list[Any] = [
    pytest.param(VALID_FOUR, {}, set(), id="valid_four"),
    pytest.param(
        [*VALID_FOUR, ("AI2", "Report for Friday", None, None)], {}, set(), id="valid_five"
    ),
    pytest.param([], {}, set(), id="empty"),
    pytest.param(
        [
            ("H60", "Due today", "10:00", "11:00"),
            ("Y30", "Client is waiting", "11:00", "11:30"),
            ("AI", "One", None, None),
            ("AI2", "Two", None, None),
            ("AI3", "Three", None, None),
            ("AI4", "Four", None, None),
        ],
        {},
        {"too_many_items"},
        id="six_items",
    ),
    pytest.param(
        [("AI", "Once", None, None), ("AI", "Twice", None, None)],
        {},
        {"duplicate_task"},
        id="duplicate",
    ),
    pytest.param([("UNKNOWN", "Who?", None, None)], {}, {"unknown_task"}, id="unknown"),
    pytest.param([("DONE", "Finished", None, None)], {}, {"ineligible_task"}, id="done_task"),
    pytest.param([("WAITING", "Blocked", None, None)], {}, {"ineligible_task"}, id="blocked_task"),
    pytest.param([("AI", "", None, None)], {}, {"missing_reason"}, id="no_reason"),
    pytest.param([("AI", "x" * 141, None, None)], {}, {"reason_too_long"}, id="long_reason"),
    pytest.param(
        [("H_NO_EST", "No estimate", "10:00", "11:00")],
        {},
        {"missing_estimate"},
        id="human_no_estimate",
    ),
    pytest.param([("H60", "No block", None, None)], {}, {"missing_block"}, id="human_no_block"),
    pytest.param(
        [("AI", "AI in a block", "10:00", "10:30")], {}, {"ai_task_has_block"}, id="ai_with_block"
    ),
    pytest.param(
        [("H90", "Over lunch", "11:30", "13:00")],
        {},
        {"block_outside_free_time"},
        id="straddles_busy",
    ),
    pytest.param(
        [("H30", "Too early", "09:30", "10:00")],
        {},
        {"block_outside_free_time"},
        id="before_window",
    ),
    pytest.param(
        [("H90", "Squeezed", "10:00", "11:00")], {}, {"block_length_mismatch"}, id="wrong_length"
    ),
    pytest.param(
        [("H60", "First", "10:00", "11:00"), ("Y60", "Second", "10:30", "11:30")],
        {},
        {"blocks_overlap"},
        id="overlap",
    ),
    pytest.param(
        [("H30", "Already late", "10:00", "10:30")],
        {"replan": True, "now": "10:20"},
        {"block_in_past"},
        id="replan_past",
    ),
    pytest.param(
        [
            ("H120", "Big one", "10:00", "12:00"),
            ("H120B", "Big two", "13:00", "15:00"),
            ("H60", "Squeezed in", "13:00", "14:00"),
        ],
        {},
        {"blocks_overlap", "exceeds_free_time"},
        id="too_much_human",
    ),
    pytest.param(
        [("AI", "One", None, None), ("AI2", "Two", None, None), ("AI3", "Three", None, None)],
        {"free": []},
        set(),
        id="ai_only_no_free_time",
    ),
    pytest.param([("Y30", "Your half", "10:00", "10:30")], {}, set(), id="hybrid_human_portion"),
]


def table_context(overrides: dict[str, Any]) -> Any:
    tasks = {}
    for number, label, estimate, status, blocked, eligible in TASKS.values():
        made = plan_task(number, label, estimate, status, blocked=blocked, eligible=eligible)
        tasks[made.task_id] = made
    return rules.PlanContext(
        day=DAY,
        tz="America/New_York",
        now=at(overrides.get("now", "08:30")),
        free_blocks=overrides.get("free", FREE),
        tasks=tasks,
        replan=overrides.get("replan", False),
    )


def table_items(items: list[Item]) -> list[Any]:
    built = []
    for position, (name, reason, start, end) in enumerate(items, start=1):
        tid = UNKNOWN if name == "UNKNOWN" else task_id(TASKS[name][0])
        block = None if start is None or end is None else span(start, end)
        built.append(rules.PlannedItem(task_id=tid, position=position, reason=reason, block=block))
    return built


@pytest.mark.req("FR-4.3")
@pytest.mark.wp("P1-11")
@pytest.mark.xfail(strict=True, reason="spec:P1-11")
@pytest.mark.parametrize(("items", "overrides", "expected"), ROWS)
def test_validate_plan_table(
    items: list[Item], overrides: dict[str, Any], expected: set[str]
) -> None:
    """T-P1-11-01
    Each row's items against the row's context give exactly the expected violation codes.
    """
    found = rules.validate_plan(table_items(items), table_context(overrides))
    assert {v.code for v in found} == expected


# --- Properties ---------------------------------------------------------------------------


@st.composite
def worlds(draw: st.DrawFn) -> dict[str, Any]:
    """A random 2026 weekday in a random zone, 0 to 6 disjoint free blocks inside 09:00 to
    18:00, and 0 to 12 tasks; as plain data, so the test builds the models."""
    zone = draw(st.sampled_from(ZONES))
    tz = ZoneInfo(zone)
    day = date(2026, 1, 1) + timedelta(days=draw(st.integers(0, 364)))
    if day.weekday() >= 5:
        day -= timedelta(days=day.weekday() - 4)
    marks = sorted(draw(st.sets(st.integers(0, 540), max_size=12)))
    pairs = [(marks[i], marks[i + 1]) for i in range(0, len(marks) - 1, 2)][:6]
    nine = datetime.combine(day, time(9), tz)
    free = [
        Interval(nine + timedelta(minutes=a), nine + timedelta(minutes=b))
        for a, b in pairs
        if (nine + timedelta(minutes=a)).astimezone(UTC)
        < (nine + timedelta(minutes=b)).astimezone(UTC)
    ]
    count = draw(st.integers(0, 12))
    tasks = []
    for n in range(1, count + 1):
        label = draw(st.sampled_from(["human", "ai", "hybrid"]))
        status = draw(st.sampled_from(["backlog", "backlog", "today", "waiting_on_human", "done"]))
        tasks.append(
            {
                "n": n,
                "label": label,
                "estimate": None if label == "ai" else draw(st.integers(5, 240)),
                "status": status,
                "due_offset": draw(st.one_of(st.none(), st.integers(-3, 10))),
                "priority": draw(st.integers(0, 3)),
                "rollover_count": draw(st.integers(0, 3)),
                "age_days": draw(st.integers(0, 30)),
            }
        )
    replan = draw(st.booleans())
    now = nine + timedelta(minutes=draw(st.integers(-120, 600)))
    return {"zone": zone, "day": day, "free": free, "tasks": tasks, "replan": replan, "now": now}


def world_context(world: dict[str, Any]) -> Any:
    tz = ZoneInfo(world["zone"])
    tasks = {}
    for t in world["tasks"]:
        made = plan_task(
            t["n"],
            t["label"],
            t["estimate"],
            t["status"],
            blocked=t["status"] == "waiting_on_human",
            eligible=t["status"] != "done",
            due_on=None if t["due_offset"] is None else world["day"] + timedelta(t["due_offset"]),
            priority=t["priority"],
            rollover_count=t["rollover_count"],
            created_at=datetime.combine(world["day"], time(7), tz) - timedelta(days=t["age_days"]),
        )
        tasks[made.task_id] = made
    return rules.PlanContext(
        day=world["day"],
        tz=world["zone"],
        now=world["now"].astimezone(UTC),
        free_blocks=world["free"],
        tasks=tasks,
        replan=world["replan"],
    )


def pickable(ctx: Any) -> list[UUID]:
    return [t.task_id for t in ctx.tasks.values() if t.eligible and not t.blocked]


def picks_of(ids: list[UUID]) -> list[Any]:
    return [rules.PlanPick(task_id=tid, reason=f"Reason {i}") for i, tid in enumerate(ids, 1)]


@pytest.mark.req("FR-4.3")
@pytest.mark.wp("P1-11")
@pytest.mark.xfail(strict=True, reason="spec:P1-11")
@PROPERTY
@given(world=worlds(), data=st.data())
def test_assigned_plans_always_validate(world: dict[str, Any], data: st.DataObject) -> None:
    """T-P1-11-02
    For any pool, picks (a random subsequence of at most 5 pickable tasks) and free blocks,
    the items `assign_blocks` places pass `validate_plan`.
    """
    ctx = world_context(world)
    pool = pickable(ctx)
    chosen = data.draw(st.lists(st.sampled_from(pool), unique=True, max_size=5)) if pool else []
    items, _unplaceable = rules.assign_blocks(picks_of(chosen), ctx)
    assert rules.validate_plan(items, ctx) == []


def fallback_key(task: Any) -> tuple[Any, ...]:
    return (
        task.due_on or date.max,
        -task.priority,
        -task.rollover_count,
        task.created_at,
        task.task_id,
    )


@pytest.mark.req("FR-4.3")
@pytest.mark.wp("P1-11")
@pytest.mark.xfail(strict=True, reason="spec:P1-11")
@PROPERTY
@given(world=worlds())
def test_fallback_always_validates_and_is_due_date_ordered(world: dict[str, Any]) -> None:
    """T-P1-11-03
    `fallback_plan` output validates, holds only eligible unblocked tasks, at most 5, and its
    items follow the fallback sort (due date with nulls last, priority highest first, most
    rolled over, oldest).
    """
    ctx = world_context(world)
    items, _unplaceable = rules.fallback_plan(ctx)
    assert rules.validate_plan(items, ctx) == []
    assert len(items) <= rules.MAX_ITEMS
    assert [i.position for i in items] == list(range(1, len(items) + 1))
    planned = [ctx.tasks[i.task_id] for i in items]
    assert all(t.eligible and not t.blocked for t in planned)
    assert planned == sorted(planned, key=fallback_key)
    assert all(i.reason for i in items)


@pytest.mark.req("FR-4.3")
@pytest.mark.wp("P1-11")
@pytest.mark.xfail(strict=True, reason="spec:P1-11")
@PROPERTY
@given(world=worlds(), data=st.data())
def test_ai_tasks_never_consume_free_time(world: dict[str, Any], data: st.DataObject) -> None:
    """T-P1-11-04
    Adding any number of AI picks, anywhere in the pick order, never changes the Human and
    Hybrid blocks or the unplaceable picks.
    """
    ctx = world_context(world)
    pool = pickable(ctx)
    people = [tid for tid in pool if ctx.tasks[tid].label != "ai"]
    machines = [tid for tid in pool if ctx.tasks[tid].label == "ai"]
    base = data.draw(st.lists(st.sampled_from(people), unique=True)) if people else []
    extra = data.draw(st.lists(st.sampled_from(machines), unique=True)) if machines else []
    mixed = list(base)
    for tid in extra:
        mixed.insert(data.draw(st.integers(0, len(mixed))), tid)

    def blocks_and_offers(ids: list[UUID]) -> tuple[dict[UUID, Any], list[Any]]:
        items, unplaceable = rules.assign_blocks(picks_of(ids), ctx)
        human = {i.task_id: i.block for i in items if ctx.tasks[i.task_id].label != "ai"}
        return human, [(u.task_id, u.offer) for u in unplaceable]

    assert blocks_and_offers(mixed) == blocks_and_offers(base)
