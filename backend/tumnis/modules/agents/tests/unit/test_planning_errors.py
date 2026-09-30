"""The planning reply rule (P1-05, FR-5.2): picks and alternates come from the request's
candidates, no task is picked twice, and there are at most `max_items` picks."""

from __future__ import annotations

from typing import Any

import pytest

from tumnis.modules.agents.rules import planning_errors
from tumnis.modules.agents.skill_io import PlanningRequest, PlanningResult

A, B, C, X = (f"01950000-0000-7000-8000-00000000082{n}" for n in (1, 2, 3, 9))


def _candidate(task_id: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "project_id": "01950000-0000-7000-8000-000000000201",
        "project_name": "Acme site",
        "title": "A task",
        "label": "human",
        "estimate_minutes": 30,
        "due_on": None,
        "priority": "normal",
        "rollover_count": 0,
        "first_action": None,
        "age_days": 1,
    }


def _request(max_items: int = 5) -> PlanningRequest:
    return PlanningRequest.model_validate(
        {
            "day": "2026-03-09",
            "timezone": "Europe/London",
            "now": "2026-03-09T07:30:00Z",
            "working_window": None,
            "free_blocks": [],
            "max_items": max_items,
            "candidates": [_candidate(A), _candidate(B), _candidate(C)],
            "projects": [],
            "agents": [],
            "events": [],
        }
    )


def _result(picks: list[str], alternates: list[str]) -> PlanningResult:
    return PlanningResult.model_validate(
        {"picks": [{"task_id": t, "reason": "Due soon"} for t in picks], "alternates": alternates}
    )


CASES: dict[str, tuple[list[str], list[str], set[str]]] = {
    "valid": ([A, B], [C], set()),
    "empty": ([], [], set()),
    "invented_pick": ([A, X], [], {"unknown_pick"}),
    "duplicate_pick": ([A, A], [], {"duplicate_pick"}),
    "invented_alternate": ([A], [X], {"unknown_alternate"}),
    "all_three": ([X, X], [X], {"unknown_pick", "duplicate_pick", "unknown_alternate"}),
}


@pytest.mark.req("FR-5.2")
@pytest.mark.wp("P1-05")
@pytest.mark.parametrize("case", sorted(CASES))
def test_planning_errors_table(case: str) -> None:
    picks, alternates, expected = CASES[case]
    assert set(planning_errors(_request(), _result(picks, alternates))) == expected


@pytest.mark.req("FR-5.2")
@pytest.mark.wp("P1-05")
def test_picks_over_max_items() -> None:
    assert planning_errors(_request(), _result([A, B, C], [])) == []
    rule_view = _request().model_copy(update={"max_items": 2})  # the rule reads max_items
    assert planning_errors(rule_view, _result([A, B, C], [])) == ["too_many_picks"]
