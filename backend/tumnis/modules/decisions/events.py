"""decisions event payload models and subscribers (P1-02).

`decisions.record_outcome` listens to `human.decided`: when the human acts on a review
item that carries a decision id, it records on that decision's log row whether they
overrode it and what they chose (FR-11.5). A label override (P1-07) says in its payload
whether the human's label differs from the AI's (`overridden`).

`decisions.label_on_create` (`task.created` without a label) and
`decisions.label_on_title_change` (`task.updated` naming `title`) queue the quick-add label
(`workflows.label_task`, P1-07, FR-4.1) unless the user has chosen the label. Idempotent:
the workflow is keyed on the event.

A subscriber's name is part of every delivery's workflow ID, so it never changes.
"""

from uuid import UUID

from tumnis.core.events import EventEnvelope, subscribe
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.decisions import api, workflows
from tumnis.modules.decisions.payloads import DecisionMadeV1, DecisionUnavailablePayload

__all__ = [
    "DecisionMadeV1",
    "DecisionUnavailablePayload",
    "label_on_create",
    "label_on_title_change",
    "record_outcome",
]

ACCEPTED = frozenset({"accept", "approve"})  # the human kept what the decision proposed
DEFERRED = frozenset({"snooze"})  # the item comes back later: not an outcome yet


@subscribe("human.decided", name="decisions.record_outcome")
async def record_outcome(envelope: EventEnvelope) -> None:
    """Idempotent: a second delivery writes the same values again."""
    decision_id = envelope.payload.get("decision_id")
    if decision_id is None or envelope.payload.get("decision") in DEFERRED:
        return
    payload = envelope.payload.get("payload") or {}
    ctx = WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)
    async with tenant_session(ctx) as session:
        overridden = payload.get("overridden")  # a label override says so itself (P1-07)
        await api.record_outcome(
            UUID(str(decision_id)),
            overridden=(
                bool(overridden)
                if overridden is not None
                else envelope.payload["decision"] not in ACCEPTED
            ),
            final_value=payload.get("value"),
            outcome_at=envelope.occurred_at,
            session=session,
        )


@subscribe("task.created", name="decisions.label_on_create")
async def label_on_create(envelope: EventEnvelope) -> None:
    if envelope.payload.get("label") is not None:
        return  # created with a label (by the user or an agent): nothing to decide
    await workflows.enqueue_label(
        envelope.workspace_id,
        UUID(str(envelope.payload["task_id"])),
        event_id=envelope.event_id,
    )


@subscribe("task.updated", name="decisions.label_on_title_change")
async def label_on_title_change(envelope: EventEnvelope) -> None:
    if "title" not in envelope.payload.get("changed_fields", ()):
        return
    await workflows.enqueue_label(
        envelope.workspace_id,
        UUID(str(envelope.payload["task_id"])),
        event_id=envelope.event_id,
    )
