"""The label rules (P1-07, FR-4.1, R-08): whether Jev may still label a task, and which
state its label chip shows."""

from __future__ import annotations

from typing import Any

import pytest

from tumnis.modules.tasks.rules import Label

CASES = [
    # (label_source, label, label_suggestion, may_auto_label, state)
    (None, None, None, True, "pending"),
    (None, None, Label.HYBRID, True, "suggested"),
    ("jev", Label.HYBRID, None, True, "confirmed"),
    ("fallback", Label.AI, None, True, "confirmed"),
    ("agent", Label.HUMAN, None, True, "confirmed"),
    ("user", Label.HUMAN, None, False, "confirmed"),
    ("user", Label.AI, Label.HYBRID, False, "confirmed"),
]


@pytest.mark.req("FR-4.1")
@pytest.mark.wp("P1-07")
@pytest.mark.parametrize(("source", "label", "suggestion", "may", "state"), CASES)
def test_may_auto_label_and_state(
    source: Any, label: Label | None, suggestion: Label | None, may: bool, state: str
) -> None:
    """T-P1-07-08
    Only a label the user chose (source `user`) stops Jev; Jev, the fallback and an agent
    leave the task open to relabelling. The chip is confirmed when the label is set,
    suggested when only a suggestion exists, and pending otherwise.
    """
    from tumnis.modules.tasks.rules import (  # noqa: PLC0415
        label_state,
        may_auto_label,
    )

    assert may_auto_label(source) is may
    assert label_state(label, suggestion) == state
