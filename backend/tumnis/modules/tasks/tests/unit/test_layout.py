"""Subtask layout against the card threshold (P0-18, FR-3.4, FR-3.8): a Human or Hybrid
subtask at or above the threshold is its own card, below it (or unestimated) a checklist
item on the parent; AI subtasks always nest; a pending label is laid out like Human."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st


@pytest.mark.req("FR-3.4")
@pytest.mark.wp("P0-18")
def test_placement_at_29_30_31_minutes() -> None:
    """T-P0-18-09
    Threshold 30: Human 29 nests, Human 30 is a card, Hybrid 31 is a card, an unestimated
    Human subtask nests (plan default), a pending 30 is a card like Human.
    """
    from tumnis.modules.tasks.rules import Label, Placement, subtask_placement  # noqa: PLC0415

    assert subtask_placement(Label.HUMAN, 29, 30) == Placement.NESTED
    assert subtask_placement(Label.HUMAN, 30, 30) == Placement.CARD
    assert subtask_placement(Label.HYBRID, 31, 30) == Placement.CARD
    assert subtask_placement(Label.HUMAN, None, 30) == Placement.NESTED
    assert subtask_placement(None, 30, 30) == Placement.CARD
    assert subtask_placement(None, 29, 30) == Placement.NESTED


@pytest.mark.req("FR-3.4")
@pytest.mark.wp("P0-18")
@given(
    estimate=st.one_of(st.none(), st.integers(min_value=1, max_value=960)),
    threshold=st.integers(min_value=1, max_value=960),
)
def test_ai_subtasks_always_nest(estimate: int | None, threshold: int) -> None:
    """T-P0-18-10
    Any estimate, any threshold: an AI subtask nests under its parent.
    """
    from tumnis.modules.tasks.rules import Label, Placement, subtask_placement  # noqa: PLC0415

    assert subtask_placement(Label.AI, estimate, threshold) == Placement.NESTED
