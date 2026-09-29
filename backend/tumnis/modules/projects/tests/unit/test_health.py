"""Project health is a pure rule (P0-17, FR-1.1): open tasks waiting on the human block a
project, overdue open tasks put it at risk, and "overdue" is judged on the workspace's
local day (REL-6)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

TODAY = date(2026, 3, 9)
DAY = timedelta(days=1)


def _health(tasks: Sequence[tuple[str, date | None]], today: date = TODAY) -> str:
    from tumnis.modules.projects.rules import TaskFacts, project_health, summarize  # noqa: PLC0415

    facts = summarize([TaskFacts(status=s, due_on=due) for s, due in tasks], today)
    return str(project_health(facts))


@pytest.mark.req("FR-1.1")
@pytest.mark.wp("P0-17")
@pytest.mark.xfail(strict=True, reason="spec:P0-17")
@pytest.mark.parametrize(
    ("tasks", "expected"),
    [
        ([], "on_track"),
        ([("today", TODAY)], "on_track"),
        ([("backlog", TODAY - DAY)], "at_risk"),
        ([("waiting_on_human", None)], "blocked"),
        ([("in_review", TODAY - DAY)], "at_risk"),
        ([("done", TODAY - 7 * DAY)], "on_track"),
        ([("backlog", TODAY + DAY)], "on_track"),
    ],
    ids=[
        "none",
        "today-due-today",
        "backlog-due-yesterday",
        "waiting-no-due",
        "in-review-due-yesterday",
        "done-due-last-week",
        "backlog-due-tomorrow",
    ],
)
def test_health_rule_table(tasks: list[tuple[str, date | None]], expected: str) -> None:
    """T-P0-17-01
    Task sets (status, due relative to today) map to Health: none, due today and due
    tomorrow are on track; an open task due yesterday is at risk (in_review too: it is not
    waiting on the human for health); waiting_on_human is blocked; done never counts.
    """
    assert _health(tasks) == expected


@pytest.mark.req("FR-1.1")
@pytest.mark.wp("P0-17")
@pytest.mark.xfail(strict=True, reason="spec:P0-17")
def test_blocked_wins_over_at_risk() -> None:
    """T-P0-17-02
    A project with a task waiting on the human and an overdue task is blocked; the
    aggregate facts alone decide it (blocked > at_risk > on_track).
    """
    from tumnis.modules.projects.rules import Health, HealthFacts, project_health  # noqa: PLC0415

    assert _health([("waiting_on_human", None), ("backlog", TODAY - DAY)]) == "blocked"
    assert project_health(HealthFacts(waiting_on_human=1, overdue=3)) is Health.BLOCKED
    assert project_health(HealthFacts(waiting_on_human=0, overdue=1)) is Health.AT_RISK
    assert project_health(HealthFacts(waiting_on_human=0, overdue=0)) is Health.ON_TRACK


@pytest.mark.req("FR-1.1", "REL-6")
@pytest.mark.wp("P0-17")
@pytest.mark.xfail(strict=True, reason="spec:P0-17")
def test_overdue_is_judged_in_workspace_timezone() -> None:
    """T-P0-17-03
    A backlog task due 2026-03-09 at 2026-03-10T03:30Z: in America/New_York (local
    2026-03-09 23:30) the project is on track; in Australia/Sydney (local 2026-03-10
    14:30) it is at risk.
    """
    from tumnis.modules.projects.rules import local_today  # noqa: PLC0415

    now = datetime(2026, 3, 10, 3, 30, tzinfo=UTC)
    tasks: list[tuple[str, date | None]] = [("backlog", date(2026, 3, 9))]
    new_york = local_today(now, ZoneInfo("America/New_York"))
    sydney = local_today(now, ZoneInfo("Australia/Sydney"))
    assert new_york == date(2026, 3, 9)
    assert sydney == date(2026, 3, 10)
    assert _health(tasks, new_york) == "on_track"
    assert _health(tasks, sydney) == "at_risk"
    with pytest.raises(ValueError, match="aware"):
        local_today(now.replace(tzinfo=None), ZoneInfo("UTC"))


@pytest.mark.req("FR-1.1")
@pytest.mark.wp("P0-17")
@pytest.mark.xfail(strict=True, reason="spec:P0-17")
def test_done_and_trashed_tasks_never_count() -> None:
    """T-P0-17-04
    Done tasks and trashed (deleted) tasks count neither as waiting nor as overdue, and
    `is_overdue` is false for them and for tasks without a due date.
    """
    from tumnis.modules.projects.rules import (  # noqa: PLC0415
        HealthFacts,
        TaskFacts,
        is_overdue,
        summarize,
    )

    tasks = [
        TaskFacts(status="done", due_on=TODAY - 7 * DAY),
        TaskFacts(status="waiting_on_human", due_on=None, deleted=True),
        TaskFacts(status="backlog", due_on=TODAY - DAY, deleted=True),
    ]
    assert summarize(tasks, TODAY) == HealthFacts(waiting_on_human=0, overdue=0)
    assert not is_overdue("done", TODAY - DAY, TODAY)
    assert not is_overdue("backlog", None, TODAY)
    assert not is_overdue("backlog", TODAY, TODAY)
    assert is_overdue("in_progress", TODAY - DAY, TODAY)
