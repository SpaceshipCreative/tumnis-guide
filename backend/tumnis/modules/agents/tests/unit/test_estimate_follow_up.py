"""`estimate_follow_up` (P1-08, review of PR #109): after an enrichment that did not ask
for the estimate, whether the task now lacks one and gets an estimate-only follow-up.

A task relabelled Human or Hybrid while its enrichment was pending or running is left to
that enrichment (`enrich_on_update` starts nothing then), whose request was built without
the estimate. A label the enrichment itself revised is not followed up here: its own
`task.updated` reaches `enrich_on_update` after the enrichment is `done`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

TASK_ID = UUID("01950000-0000-7000-8000-000000000811")
PROJECT_ID = UUID("01950000-0000-7000-8000-000000000812")
FA, AC, EST = "first_action", "acceptance_criteria", "estimate_minutes"


def _snapshot(**fields: Any) -> Any:
    from tumnis.modules.agents.rules import TaskSnapshot  # noqa: PLC0415

    base: dict[str, Any] = {
        "id": TASK_ID,
        "project_id": PROJECT_ID,
        "title": "Send the March invoice",
        "label": "human",
        "label_source": "user",
        "status": "backlog",
        "first_action": "Open last month's invoice and duplicate it",
        "first_action_source": "agent",
        "acceptance_criteria": "- The invoice is sent",
        "estimate_minutes": None,
        "version": 4,
        "enrichment_status": "done",
    }
    return TaskSnapshot.model_validate(base | fields)


# case -> (requested, snapshot fields, follow up?)
CASES: dict[str, tuple[list[str], dict[str, Any], bool]] = {
    "relabelled_human_by_user": ([FA, AC], {}, True),
    "relabelled_hybrid_by_jev": ([FA, AC], {"label": "hybrid", "label_source": "jev"}, True),
    "estimate_was_requested": ([FA, AC, EST], {}, False),
    "estimate_filled_meanwhile": ([FA, AC], {"estimate_minutes": 30}, False),
    "still_ai": ([FA, AC], {"label": "ai"}, False),
    "label_pending": ([FA, AC], {"label": None, "label_source": None}, False),
    "revised_by_the_agent": ([FA, AC], {"label_source": "agent"}, False),
    "task_done": ([FA, AC], {"status": "done"}, False),
}


@pytest.mark.req("FR-4.4")
@pytest.mark.wp("P1-08")
@pytest.mark.parametrize("case", list(CASES))
def test_estimate_follow_up(case: str) -> None:
    from tumnis.modules.agents.rules import estimate_follow_up  # noqa: PLC0415

    requested, fields, expected = CASES[case]
    assert estimate_follow_up(requested, _snapshot(**fields)) is expected
