"""What an enrichment fills (P1-08, FR-4.4): one table feeds the three places the rule
lives, so they cannot drift apart (plan: "one parametrized test table feeds all three").

- `missing_fields`: first action when empty or still the placeholder; acceptance criteria
  when empty; estimate only for human and hybrid tasks without one. AI tasks never list
  an estimate, and a pending label (NULL, R-08) lists none either.
- `enrichment_errors` (P1-05): a result for the case carries an estimate exactly when
  the label is human or hybrid, so a well-formed one has no errors and an AI task's
  estimate is refused.
- `merge_enrichment`: fills exactly the fields that were missing, from that result.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

TASK_ID = UUID("01950000-0000-7000-8000-000000000801")
PROJECT_ID = UUID("01950000-0000-7000-8000-000000000802")
FIRST_ACTION = "Open last month's invoice and duplicate it"
CRITERIA = ["The invoice is sent to the client", "The amount matches the rate card"]
ESTIMATE = 20
SPLIT = {"ai_portion": "Draft the invoice lines", "human_portion": "Check and send it"}

FA, AC, EST = "first_action", "acceptance_criteria", "estimate_minutes"

# case -> (label, first action, its source, criteria, estimate, status, missing)
CASES: dict[str, tuple[Any, ...]] = {
    "human_all_missing": ("human", None, None, None, None, "backlog", [FA, AC, EST]),
    "human_complete": ("human", "Call Dana", None, "- Dana said yes", 30, "backlog", []),
    "human_placeholder_first_action": (
        "human",
        "Open the invoice template",
        "placeholder",
        "- Sent",
        30,
        "backlog",
        [FA],
    ),
    "human_agent_first_action_kept": ("human", "Call Dana", "agent", None, 30, "today", [AC]),
    "human_blank_first_action": ("human", "   ", None, "- Sent", 30, "backlog", [FA]),
    "human_empty_criteria": ("human", "Call Dana", None, "", 30, "backlog", [AC]),
    "human_no_estimate": ("human", "Call Dana", None, "- Sent", None, "in_progress", [EST]),
    "hybrid_all_missing": ("hybrid", None, None, None, None, "backlog", [FA, AC, EST]),
    "hybrid_no_estimate": ("hybrid", "Call Dana", None, "- Sent", None, "backlog", [EST]),
    "ai_all_missing": ("ai", None, None, None, None, "backlog", [FA, AC]),
    "ai_complete": ("ai", "Run the export", None, "- Exported", None, "backlog", []),
    "ai_placeholder": ("ai", "Run the export", "placeholder", None, None, "backlog", [FA, AC]),
    "pending_label_all_missing": (None, None, None, None, None, "backlog", [FA, AC]),
    "pending_label_complete": (None, "Call Dana", None, "- Sent", None, "backlog", []),
    "done_task_all_missing": ("human", None, None, None, None, "done", [FA, AC, EST]),
}


def _snapshot(  # noqa: PLR0917  # one table row, spelled out
    label: Any,
    first_action: Any,
    source: Any,
    criteria: Any,
    estimate: Any,
    status: str,
    **extra: Any,
) -> Any:
    from tumnis.modules.agents.rules import TaskSnapshot  # noqa: PLC0415

    return TaskSnapshot(
        id=TASK_ID,
        project_id=PROJECT_ID,
        title="Send Acme the March invoice",
        label=label,
        label_source=extra.pop("label_source", None if label is None else "user"),
        status=status,
        first_action=first_action,
        first_action_source=source,
        acceptance_criteria=criteria,
        estimate_minutes=estimate,
        version=extra.pop("version", 3),
        **extra,
    )


def _result(label: Any, **overrides: Any) -> Any:
    """A well-formed result for a task labelled `label`: an estimate for human and hybrid
    tasks, a split for hybrid ones."""
    from tumnis.modules.agents.skill_io import EnrichmentResult  # noqa: PLC0415

    body: dict[str, Any] = {
        "task_id": str(TASK_ID),
        "first_action": FIRST_ACTION,
        "acceptance_criteria": CRITERIA,
        "estimate_minutes": ESTIMATE if label in {"human", "hybrid"} else None,
        "hybrid_split": SPLIT if label == "hybrid" else None,
    }
    body.update(overrides)
    return EnrichmentResult.model_validate(body)


def _request(snapshot: Any, missing: list[str]) -> Any:
    from tumnis.modules.agents.skill_io import EnrichmentRequest  # noqa: PLC0415

    return EnrichmentRequest.model_validate(
        {
            "task": {
                "id": str(snapshot.id),
                "title": snapshot.title,
                "label": snapshot.label,
                "first_action": snapshot.first_action,
                "acceptance_criteria": snapshot.acceptance_criteria,
                "estimate_minutes": snapshot.estimate_minutes,
            },
            "missing": missing,
            "project": {"name": "Acme site"},
            "brief": "",
            "passages": [],
            "estimate_history": [],
        }
    )


@pytest.mark.req("FR-4.4")
@pytest.mark.wp("P1-08")
@pytest.mark.parametrize("case", sorted(CASES))
def test_missing_fields_table(case: str) -> None:
    """T-P1-08-01
    Every combination of label, first action, criteria, estimate and placeholder source:
    `missing_fields` names exactly the case's fields, `needs_enrichment` holds when it
    names any and the task is not done, a well-formed result breaks no cross-field rule
    (an AI task's estimate does), and `merge_enrichment` fills exactly those fields.
    """
    from tumnis.modules.agents.rules import (  # noqa: PLC0415
        enrichment_errors,
        merge_enrichment,
        missing_fields,
        needs_enrichment,
    )
    from tumnis.modules.agents.skill_io import ESTIMATE_RANGE  # noqa: PLC0415

    label, first_action, source, criteria, estimate, status, missing = CASES[case]
    snapshot = _snapshot(label, first_action, source, criteria, estimate, status)

    assert missing_fields(snapshot) == missing
    assert needs_enrichment(snapshot) is (bool(missing) and status != "done")
    if EST not in missing:
        assert label not in {"human", "hybrid"} or estimate is not None

    if not missing:
        return
    good = _result(label)
    request = _request(snapshot, missing)
    assert enrichment_errors(request, good) == []
    if label == "ai":
        assert enrichment_errors(request, _result(label, estimate_minutes=45)) == [
            "estimate_for_ai"
        ]

    patch = merge_enrichment(snapshot, good, requested=missing, estimate_range=ESTIMATE_RANGE)
    assert (patch.first_action is not None) is (FA in missing)
    assert (patch.acceptance_criteria is not None) is (AC in missing)
    assert (patch.estimate_minutes is not None) is (EST in missing)
    assert patch.label is None
    if FA in missing:
        assert patch.first_action == FIRST_ACTION
    if EST in missing:
        assert patch.estimate_minutes == ESTIMATE
    if AC in missing:
        text = patch.acceptance_criteria or ""
        assert [f"- {line}" for line in CRITERIA] == text.splitlines()[: len(CRITERIA)]
        assert ("AI part: Draft the invoice lines" in text) is (label == "hybrid")
        assert ("Your part: Check and send it" in text) is (label == "hybrid")


@pytest.mark.req("FR-4.4", "UX 9")
@pytest.mark.wp("P1-08")
def test_merge_keeps_what_the_user_wrote_meanwhile() -> None:
    """A field requested at the start but filled by the user before the result arrived is
    left alone; the other requested fields are still filled (UX 9)."""
    from tumnis.modules.agents.rules import merge_enrichment  # noqa: PLC0415
    from tumnis.modules.agents.skill_io import ESTIMATE_RANGE  # noqa: PLC0415

    now = _snapshot("human", "Ring Dana first", None, None, None, "backlog")
    patch = merge_enrichment(
        now, _result("human"), requested=[FA, AC, EST], estimate_range=ESTIMATE_RANGE
    )
    assert patch.first_action is None
    assert patch.acceptance_criteria is not None
    assert patch.estimate_minutes == ESTIMATE


@pytest.mark.req("FR-4.1", "FR-4.4")
@pytest.mark.wp("P1-08")
@pytest.mark.parametrize(
    ("label", "label_source", "applies"),
    [
        ("ai", "jev", True),
        ("ai", "fallback", True),
        (None, None, True),
        ("ai", "user", False),
        ("ai", "agent", False),
    ],
)
def test_merge_revises_only_an_ai_or_pending_label(
    label: Any, label_source: Any, applies: bool
) -> None:
    """A label revision applies over a `jev` or `fallback` label or a pending one (R-08),
    never over the user's; revised to human, the task also takes the estimate."""
    from tumnis.modules.agents.rules import merge_enrichment  # noqa: PLC0415
    from tumnis.modules.agents.skill_io import ESTIMATE_RANGE  # noqa: PLC0415

    now = _snapshot(label, None, None, None, None, "backlog", label_source=label_source)
    revised = _result("human", label_revision={"label": "human", "reason": "Needs a call"})
    patch = merge_enrichment(now, revised, requested=[FA, AC], estimate_range=ESTIMATE_RANGE)
    assert (patch.label == "human") is applies
    assert (patch.label_reason == "Needs a call") is applies
    assert (patch.estimate_minutes == ESTIMATE) is applies
    assert patch.first_action == FIRST_ACTION


@pytest.mark.req("FR-4.4")
@pytest.mark.wp("P1-08")
def test_merge_never_estimates_an_ai_task_or_out_of_range() -> None:
    """No estimate for an AI task even when a result carries one, and none outside
    `ESTIMATE_RANGE` (R-11)."""
    from tumnis.modules.agents.rules import merge_enrichment  # noqa: PLC0415

    ai = _snapshot("ai", None, None, None, None, "backlog")
    patch = merge_enrichment(
        ai, _result("ai", estimate_minutes=45), requested=[FA, AC, EST], estimate_range=(5, 960)
    )
    assert patch.estimate_minutes is None
    human = _snapshot("human", None, None, None, None, "backlog")
    patch = merge_enrichment(
        human, _result("human", estimate_minutes=900), requested=[EST], estimate_range=(5, 480)
    )
    assert patch.estimate_minutes is None


class _Score:
    def __init__(self, score: float) -> None:
        self.score = score


@pytest.mark.req("FR-4.4", "FR-11.4")
@pytest.mark.wp("P1-08")
@pytest.mark.parametrize(
    ("score", "route", "flag"),
    [
        (0.25, "applied", "too_low"),
        (0.5, "applied", "too_low"),
        (0.51, "applied", None),
        (2.0, "applied", None),
        (3.49, "applied", None),
        (3.5, "applied", "too_high"),
        (3.9, "applied", "too_high"),
        (0.1, "deterministic", None),
        (3.9, "review", None),
        (None, "applied", None),
    ],
)
def test_plausibility_flag_levels(score: float | None, route: str, flag: str | None) -> None:
    """Only an applied Score flags, and only at the outer levels: <= 0.5 too low, >= 3.5
    too high."""
    from tumnis.modules.agents.rules import plausibility_flag  # noqa: PLC0415

    answer = None if score is None else _Score(score)
    assert plausibility_flag(answer, route) == flag
