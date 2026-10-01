"""Close the day (P1-18, J7): `day_summary` sorts one local day's facts into what shipped,
what agents finished, how many tasks agents prepared, what is queued overnight (empty
until P4-04) and what rolls over tonight. The day is the workspace's local day (REL-6)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from tumnis.modules.planning import rules

NEW_YORK = ZoneInfo("America/New_York")
MONDAY = date(2026, 3, 9)  # EDT (UTC-4): the local day runs 04:00Z to 04:00Z
PROJECT = UUID("0199aa00-0000-7000-8000-000000000001")


def _id(n: int) -> UUID:
    return UUID(f"0199aa00-0000-7000-8000-{n:012d}")


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def task(n: int, title: str, label: str | None, status: str, **kw: Any) -> rules.TaskFacts:
    completed = kw.pop("completed", None)
    result = kw.pop("result", None)
    return rules.TaskFacts(
        task_id=_id(n),
        project_id=PROJECT,
        title=title,
        label=label,
        status=status,
        completed_at=None if completed is None else _utc(completed),
        rollover_count=kw.pop("rollovers", 0),
        result_posted_at=None if result is None else _utc(result),
    )


def run(n: int, kind: str, status: str, finished: str | None) -> rules.RunFacts:
    return rules.RunFacts(
        run_id=_id(100 + n),
        task_id=None,
        kind=kind,
        status=status,
        finished_at=None if finished is None else _utc(finished),
    )


def _ref(t: rules.TaskFacts) -> rules.TaskRef:
    return rules.TaskRef(task_id=t.task_id, project_id=t.project_id, title=t.title, label=t.label)


def _rolls(t: rules.TaskFacts) -> rules.RolloverRef:
    return rules.RolloverRef(
        **_ref(t).model_dump(), rollover_count=t.rollover_count, tonight=t.rollover_count + 1
    )


# A full Monday: two shipped (one AI), one done late on Sunday and one just after
# midnight on Tuesday (both local), an AI task in review with a result posted today,
# two Today tasks rolling over, and agent runs of every kind and outcome.
SHIP_HUMAN = task(1, "Write Acme proposal", "human", "done", completed="2026-03-09T15:00:00")
SHIP_AI = task(2, "Summarise the brief", "ai", "done", completed="2026-03-09T20:00:00")
SUNDAY_LATE = task(3, "Sort receipts", "hybrid", "done", completed="2026-03-09T03:30:00")
TUESDAY_EARLY = task(4, "Back up photos", "human", "done", completed="2026-03-10T04:30:00")
RESULT_TODAY = task(5, "Draft the FAQ", "ai", "in_review", result="2026-03-09T18:00:00")
RESULT_SUNDAY = task(6, "Draft the intro", "ai", "in_review", result="2026-03-09T03:00:00")
ROLL_TWICE = task(7, "Call the printer", "human", "today", rollovers=2)
ROLL_NEW = task(8, "Book the venue", "hybrid", "today")
BACKLOG = task(9, "Someday idea", "human", "backlog")
RUNS = [
    run(1, "enrich", "succeeded", "2026-03-09T14:00:00"),
    run(2, "enrich", "failed", "2026-03-09T14:05:00"),
    run(3, "enrich", "succeeded", "2026-03-09T02:00:00"),  # Sunday, local
    run(4, "task", "succeeded", "2026-03-09T16:00:00"),
    run(5, "enrich", "running", None),
]

CASES = {
    "full_day": (
        [
            SHIP_HUMAN,
            SHIP_AI,
            SUNDAY_LATE,
            TUESDAY_EARLY,
            RESULT_TODAY,
            RESULT_SUNDAY,
            ROLL_TWICE,
            ROLL_NEW,
            BACKLOG,
        ],
        RUNS,
        {
            # shipped in completion order; agents' work in the order it finished;
            # rollovers by title
            "shipped": [_ref(SHIP_HUMAN), _ref(SHIP_AI)],
            "agents_finished": [_ref(RESULT_TODAY), _ref(SHIP_AI)],
            "prepared_by_agents": 1,
            "queued_overnight": [],
            "rolls_over": [_rolls(ROLL_NEW), _rolls(ROLL_TWICE)],
        },
    ),
    "empty_day": (
        [BACKLOG],
        [],
        {
            "shipped": [],
            "agents_finished": [],
            "prepared_by_agents": 0,
            "queued_overnight": [],
            "rolls_over": [],
        },
    ),
    # An AI task done today that also had its result posted today is listed once.
    "ai_done_with_result": (
        [
            task(
                10,
                "Tag the photos",
                "ai",
                "done",
                completed="2026-03-09T21:00:00",
                result="2026-03-09T19:00:00",
            )
        ],
        [run(6, "enrich", "succeeded", "2026-03-10T03:59:00")],  # 23:59 local
        {
            "shipped": [_ref(task(10, "Tag the photos", "ai", "done"))],
            "agents_finished": [_ref(task(10, "Tag the photos", "ai", "done"))],
            "prepared_by_agents": 1,
            "queued_overnight": [],
            "rolls_over": [],
        },
    ),
}


@pytest.mark.req("J7")
@pytest.mark.wp("P1-18")
@pytest.mark.parametrize("case", list(CASES))
def test_sections(case: str) -> None:
    """T-P1-18-01
    Shipped, agents finished, the prepared count and what rolls over, from a fixture day,
    with the local day's boundaries in New York.
    """
    tasks, runs, expected = CASES[case]
    summary = rules.day_summary(tasks, runs, MONDAY, NEW_YORK)
    assert summary.model_dump() == rules.DaySummary(**expected).model_dump()


@pytest.mark.req("J7", "REL-6")
@pytest.mark.wp("P1-18")
def test_day_boundary_uses_workspace_timezone() -> None:
    """T-P1-18-02
    A task done at 23:30 local counts today even though it is already tomorrow in UTC.
    """
    late = task(1, "Late finish", "human", "done", completed="2026-03-10T03:30:00")  # 23:30 EDT
    assert late.completed_at is not None
    assert late.completed_at.date() == date(2026, 3, 10)

    monday = rules.day_summary([late], [], MONDAY, NEW_YORK)
    tuesday = rules.day_summary([late], [], date(2026, 3, 10), NEW_YORK)

    assert [ref.title for ref in monday.shipped] == ["Late finish"]
    assert tuesday.shipped == []
