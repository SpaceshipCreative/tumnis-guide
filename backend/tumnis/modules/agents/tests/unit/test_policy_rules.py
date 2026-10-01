"""The approval policy rule (P2-05, FR-5.6, SAF-1): the server, not the prompt, decides
what needs a human's approval. Gated classes always do, every action on a tainted run
does, allowed classes go through, and an action the policy does not name needs approval
unless the approval-need decision point is confident it is safe."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

THRESHOLD = 0.8
DOWN = "down"  # Decisions was asked and nobody answered
NOT_ASKED = None  # the caller has not asked Decisions yet (threshold None)

# (action, tainted, noul, moved to gated by the project, expected outcome, expected rule):
# the plan's verdict table (policy = FR-5.6 default, threshold 0.8).
TABLE = [
    ("merge_main", False, None, False, "approval_required", "gated_by_policy"),
    ("open_pull_request", False, None, False, "allowed", "allowed_by_policy"),
    ("open_pull_request", True, None, False, "approval_required", "tainted_run"),
    ("read", True, None, False, "approval_required", "tainted_run"),
    ("rotate_dns_record", False, NOT_ASKED, False, "approval_required", "unknown_needs_decision"),
    ("rotate_dns_record", False, DOWN, False, "approval_required", "decisions_unavailable"),
    ("rotate_dns_record", False, (0.1, 0.6), False, "approval_required", "unknown_below_threshold"),
    ("rotate_dns_record", False, (0.1, 0.9), False, "allowed", "unknown_decided_safe"),
    ("rotate_dns_record", False, (0.7, 0.9), False, "approval_required", "unknown_decided_gated"),
    ("push_feature_branch", False, None, True, "approval_required", "gated_by_policy"),
]


def _verdict(action: str, tainted: bool, noul: Any, moved: bool) -> Any:
    from tumnis.modules.agents.rules import (  # noqa: PLC0415
        DEFAULT_POLICY,
        NoulAnswer,
        PolicySnapshot,
        approval_need,
    )

    policy = DEFAULT_POLICY
    if moved:
        policy = PolicySnapshot(
            gated=DEFAULT_POLICY.gated | {action}, allowed=DEFAULT_POLICY.allowed - {action}
        )
    if noul is NOT_ASKED:
        answer, threshold = None, None
    elif noul == DOWN:
        answer, threshold = None, THRESHOLD
    else:
        p, confidence = noul
        answer, threshold = NoulAnswer(p=p, confidence=confidence, fallback=False), THRESHOLD
    return approval_need(action, policy, run_tainted=tainted, noul=answer, threshold=threshold)


@pytest.mark.req("FR-5.6")
@pytest.mark.wp("P2-05")
@pytest.mark.parametrize(
    ("action", "tainted", "noul", "moved", "outcome", "rule"),
    TABLE,
    ids=[f"{row[0]}-{row[5]}" for row in TABLE],
)
def test_approval_need_table(  # noqa: PLR0917  # one argument per column of the table
    action: str, tainted: bool, noul: Any, moved: bool, outcome: str, rule: str
) -> None:
    """T-P2-05-08
    Every row of the verdict table: gated classes need approval, allowed ones go through,
    a tainted run needs approval for everything, and an unknown class needs approval
    unless Decisions answered confidently that it is safe."""
    verdict = _verdict(action, tainted, noul, moved)
    assert (verdict.outcome, verdict.rule) == (outcome, rule)


ACTIONS = st.one_of(
    st.sampled_from(["merge_main", "read", "open_pull_request", "rotate_dns_record"]),
    st.text(min_size=1, max_size=40),
)
NOULS = st.one_of(
    st.none(),
    st.tuples(st.floats(0, 1), st.floats(0, 1), st.booleans()),
)


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-05")
@given(
    action=ACTIONS,
    gated=st.frozensets(ACTIONS, max_size=5),
    allowed=st.frozensets(ACTIONS, max_size=5),
    noul=NOULS,
    threshold=st.one_of(st.none(), st.floats(0, 1)),
)
def test_tainted_run_every_action_needs_approval(
    action: str,
    gated: frozenset[str],
    allowed: frozenset[str],
    noul: tuple[float, float, bool] | None,
    threshold: float | None,
) -> None:
    """T-P2-05-09
    For any action, any policy and any answer from Decisions, a tainted run gets
    `approval_required` with rule `tainted_run`."""
    from tumnis.modules.agents.rules import (  # noqa: PLC0415
        NoulAnswer,
        PolicySnapshot,
        approval_need,
    )

    answer = None if noul is None else NoulAnswer(p=noul[0], confidence=noul[1], fallback=noul[2])
    verdict = approval_need(
        action,
        PolicySnapshot(gated=gated, allowed=allowed - gated),
        run_tainted=True,
        noul=answer,
        threshold=threshold,
    )
    assert verdict.outcome == "approval_required"
    assert verdict.rule == "tainted_run"
