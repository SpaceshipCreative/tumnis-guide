"""The stuck run's step rule (P4-02, FR-10.5): a task made with a stuck run's token is the
one first step under the stuck task, and a step the person takes fits in 10 minutes."""

from __future__ import annotations

import uuid

import pytest

STUCK = uuid.uuid4()
OTHER = uuid.uuid4()


@pytest.mark.req("FR-10.5")
@pytest.mark.wp("P4-02")
def test_stuck_create_task_rules() -> None:
    """T-P4-02-04
    Table: a Human or Hybrid step of 10 minutes is allowed and 11 refused
    (`stuck_step_too_long`, 422); a parent other than the stuck task is refused
    (`stuck_step_parent`, 422); a second subtask of the same stuck run is refused
    (`stuck_step_taken`, 409); an AI step carries no estimate and is allowed.
    """
    from tumnis.modules.tasks.rules import (  # noqa: PLC0415
        STUCK_MAX_MINUTES,
        Label,
        stuck_step_refusal,
    )

    assert STUCK_MAX_MINUTES == 10
    cases: list[tuple[uuid.UUID | None, Label | None, int | None, int, str | None]] = [
        (STUCK, Label.HUMAN, 10, 0, None),
        (STUCK, Label.HYBRID, 10, 0, None),
        (STUCK, Label.HUMAN, 1, 0, None),
        (STUCK, Label.HUMAN, 11, 0, "stuck_step_too_long"),
        (STUCK, Label.HYBRID, 11, 0, "stuck_step_too_long"),
        (OTHER, Label.HUMAN, 5, 0, "stuck_step_parent"),
        (None, Label.HUMAN, 5, 0, "stuck_step_parent"),
        (STUCK, Label.HUMAN, 5, 1, "stuck_step_taken"),
        (STUCK, Label.AI, None, 0, None),
    ]
    for parent, label, estimate, before, expected in cases:
        refusal = stuck_step_refusal(STUCK, parent, label, estimate, before)
        got = None if refusal is None else refusal.code
        assert got == expected, (parent, label, estimate, before)
        if refusal is not None:
            assert refusal.status == (409 if expected == "stuck_step_taken" else 422)
            assert refusal.detail
