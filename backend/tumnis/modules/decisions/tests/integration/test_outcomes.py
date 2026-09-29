"""Decision outcomes (P1-02, FR-11.5): when the human acts on a decision, `human.decided`
records whether they overrode it and what they chose, on the decision's log row."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

from tumnis.modules.decisions.tests._cases import stored_inputs

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.usefixtures("core_db", "test_cache"),
]


@pytest.mark.req("FR-11.5")
@pytest.mark.wp("P1-02")
async def test_human_override_recorded_on_log(
    workspace: WorkspaceHandle, providers: Any, clock: FixedClock, owner_session: AsyncSession
) -> None:
    """T-P1-02-14
    A `human.decided` event for a label override (decision `edit`, the new label in
    `payload.value`, the decision's id in `decision_id`) sets `overridden = true`,
    `final_value` and `outcome_at` on the matching log row and on no other; delivering it
    again changes nothing.
    """
    from tests.fixtures import make_envelope  # noqa: PLC0415
    from tumnis.core.events import run_subscriber  # noqa: PLC0415
    from tumnis.modules.decisions import events as _events  # noqa: F401, PLC0415
    from tumnis.modules.decisions.api import SubjectRef, decide  # noqa: PLC0415
    from tumnis.modules.decisions.catalog import DecisionPoint  # noqa: PLC0415

    task_id = uuid.uuid4()
    decision = await decide(
        DecisionPoint.QUICK_ADD_LABEL,
        stored_inputs("quick_add_label"),
        subject=SubjectRef(type="task", id=task_id),
        project_id=None,
        providers=providers,
        clock=clock,
    )
    other = await decide(
        DecisionPoint.ACTIONABILITY,
        stored_inputs("actionability"),
        subject=SubjectRef(type="message", id=uuid.uuid4()),
        project_id=None,
        providers=providers,
        clock=clock,
    )
    envelope = make_envelope(
        "human.decided",
        {
            "item_kind": "task_label",
            "item_id": str(task_id),
            "target_type": "task",
            "target_id": str(task_id),
            "decision": "edit",
            "previous": {"label": decision.value},
            "payload": {"value": "ai"},
            "decision_id": str(decision.decision_id),
        },
        workspace,
    )
    await run_subscriber(envelope, "decisions.record_outcome")
    await run_subscriber(envelope, "decisions.record_outcome")

    await owner_session.commit()
    rows = {
        row.id: row
        for row in (
            await owner_session.execute(
                text("SELECT id, overridden, final_value, outcome_at FROM decision_log")
            )
        ).all()
    }
    mine = rows[decision.decision_id]
    assert mine.overridden is True
    assert mine.final_value == "ai"
    assert mine.outcome_at == envelope.occurred_at
    untouched = rows[other.decision_id]
    assert (untouched.overridden, untouched.final_value, untouched.outcome_at) == (None, None, None)
