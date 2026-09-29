"""Estimates where FR-4.4 requires them (P0-18): an agent must give minutes of human time
for Human and Hybrid work, AI-only work carries none, a pending label never needs one."""

from __future__ import annotations

import pytest


@pytest.mark.req("FR-4.4")
@pytest.mark.wp("P0-18")
def test_agent_human_or_hybrid_without_estimate_is_rejected() -> None:
    """T-P0-18-06
    `normalize_estimate(HUMAN or HYBRID, None, AGENT)` raises EstimateRequired
    (`estimate_required`); a human may skip it in phase 0 (None comes back); a given
    estimate is kept; a pending label never needs one.
    """
    from tumnis.modules.tasks.rules import (  # noqa: PLC0415
        ActorKind,
        EstimateRequired,
        Label,
        normalize_estimate,
    )

    for label in (Label.HUMAN, Label.HYBRID):
        with pytest.raises(EstimateRequired) as refused:
            normalize_estimate(label, None, ActorKind.AGENT)
        assert refused.value.code == "estimate_required"
        assert normalize_estimate(label, None, ActorKind.HUMAN) is None
        assert normalize_estimate(label, 45, ActorKind.AGENT) == 45
    assert normalize_estimate(None, None, ActorKind.AGENT) is None
    assert normalize_estimate(None, 20, ActorKind.AGENT) == 20


@pytest.mark.req("FR-4.4")
@pytest.mark.wp("P0-18")
@pytest.mark.parametrize("estimate", [None, 1, 30, 960])
def test_ai_only_estimate_is_forced_empty(estimate: int | None) -> None:
    """T-P0-18-07
    An AI task's estimate is dropped whoever gives it.
    """
    from tumnis.modules.tasks.rules import ActorKind, Label, normalize_estimate  # noqa: PLC0415

    for actor in ActorKind:
        assert normalize_estimate(Label.AI, estimate, actor) is None
