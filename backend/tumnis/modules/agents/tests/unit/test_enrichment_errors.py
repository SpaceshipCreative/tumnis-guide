"""The enrichment cross-field rule the skill harness and P1-08 share (P1-05, FR-4.4).

The effective label is the result's label revision when it has one, else the task's.
An estimate (minutes of human time) is present exactly for human and hybrid tasks, a
hybrid split exactly for hybrid ones, and the result names the requested task.
"""

from __future__ import annotations

from typing import Any

import pytest

TASK_ID = "01950000-0000-7000-8000-000000000501"
OTHER_ID = "01950000-0000-7000-8000-000000000502"

# case -> (task label, revised label or None, estimate?, split?, result task id, errors)
CASES: dict[str, tuple[str, str | None, bool, bool, str, set[str]]] = {
    "human_with_estimate": ("human", None, True, False, TASK_ID, set()),
    "human_without_estimate": ("human", None, False, False, TASK_ID, {"estimate_missing"}),
    "human_with_split": ("human", None, True, True, TASK_ID, {"hybrid_split_unexpected"}),
    "ai_bare": ("ai", None, False, False, TASK_ID, set()),
    "ai_with_estimate": ("ai", None, True, False, TASK_ID, {"estimate_for_ai"}),
    "ai_with_split": ("ai", None, False, True, TASK_ID, {"hybrid_split_unexpected"}),
    "ai_with_both": (
        "ai",
        None,
        True,
        True,
        TASK_ID,
        {"estimate_for_ai", "hybrid_split_unexpected"},
    ),
    "hybrid_complete": ("hybrid", None, True, True, TASK_ID, set()),
    "hybrid_without_split": ("hybrid", None, True, False, TASK_ID, {"hybrid_split_missing"}),
    "hybrid_without_estimate": ("hybrid", None, False, True, TASK_ID, {"estimate_missing"}),
    "hybrid_bare": (
        "hybrid",
        None,
        False,
        False,
        TASK_ID,
        {"estimate_missing", "hybrid_split_missing"},
    ),
    "revised_to_ai_drops_estimate": ("human", "ai", False, False, TASK_ID, set()),
    "revised_to_ai_keeps_estimate": ("human", "ai", True, False, TASK_ID, {"estimate_for_ai"}),
    "revised_to_hybrid_from_ai": ("ai", "hybrid", True, True, TASK_ID, set()),
    "revised_to_hybrid_without_split": (
        "ai",
        "hybrid",
        True,
        False,
        TASK_ID,
        {"hybrid_split_missing"},
    ),
    "revised_to_human_from_hybrid": (
        "hybrid",
        "human",
        True,
        True,
        TASK_ID,
        {"hybrid_split_unexpected"},
    ),
    "revised_to_human_from_ai": ("ai", "human", False, False, TASK_ID, {"estimate_missing"}),
    "other_task": ("human", None, True, False, OTHER_ID, {"task_id_mismatch"}),
    "other_task_and_ai_estimate": (
        "ai",
        None,
        True,
        False,
        OTHER_ID,
        {"task_id_mismatch", "estimate_for_ai"},
    ),
}


def _request(label: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "task": {"id": TASK_ID, "title": "Send Acme the March invoice", "label": label},
        "missing": ["first_action", "acceptance_criteria", "estimate_minutes"],
        "project": {"name": "Acme site", "client": "Acme", "goal": "Launch the new site"},
        "brief": "Acme's marketing site rebuild.",
        "passages": [],
        "estimate_history": [],
    }


def _result(task_id: str, revision: str | None, *, estimate: bool, split: bool) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "task_id": task_id,
        "first_action": "Open last month's invoice and duplicate it",
        "acceptance_criteria": ["The March invoice is sent to Acme"],
        "estimate_minutes": 30 if estimate else None,
        "label_revision": (
            None if revision is None else {"label": revision, "reason": "Needs a signature"}
        ),
        "hybrid_split": (
            {"ai_portion": "Draft the invoice", "human_portion": "Check and send it"}
            if split
            else None
        ),
    }


@pytest.mark.req("FR-4.4")
@pytest.mark.wp("P1-05")
@pytest.mark.parametrize("case", sorted(CASES))
def test_cross_field_rules(case: str) -> None:
    """T-P1-05-10
    Over label, label revision, estimate and split presence (and the task id), the
    enrichment result breaks exactly the listed cross-field rules.
    """
    from tumnis.modules.agents.rules import enrichment_errors  # noqa: PLC0415
    from tumnis.modules.agents.skill_io import (  # noqa: PLC0415
        EnrichmentRequest,
        EnrichmentResult,
    )

    label, revision, estimate, split, task_id, expected = CASES[case]
    req = EnrichmentRequest.model_validate(_request(label))
    res = EnrichmentResult.model_validate(
        _result(task_id, revision, estimate=estimate, split=split)
    )

    errors = enrichment_errors(req, res)

    assert len(errors) == len(set(errors))
    assert set(errors) == expected
