"""What "N more today" counts at Guardrail (P4-01, FR-10.6): the plan's tasks still to do, in
position order (accepted items only; Done and waiting-on-the-human ones left out)."""

from __future__ import annotations

from uuid import UUID

import pytest

from tumnis.modules.focus.rules import PlanItemLite, TaskLite, guardrail_tasks

A, B, C = (UUID(f"0199aa00-0000-7000-8000-00000000f1b{n}") for n in range(3))


@pytest.mark.req("FR-10.6")
@pytest.mark.wp("P4-01")
def test_guardrail_tasks_in_order() -> None:
    plan = [PlanItemLite(C, 2, True), PlanItemLite(A, 0, True), PlanItemLite(B, 1, False)]
    tasks = {A: TaskLite("in_progress"), B: TaskLite("today"), C: TaskLite("today")}
    assert guardrail_tasks(plan, tasks) == [A, C]
    assert guardrail_tasks(plan, {A: TaskLite("done"), C: TaskLite("waiting_on_human")}) == []
