"""decisions event payload models and subscribers (P1-02).

`decisions.record_outcome` listens to `human.decided`: when the human acts on a review
item that carries a decision id, it records on that decision's log row whether they
overrode it and what they chose (FR-11.5). The subscriber's name is part of every
delivery's workflow ID, so it never changes.
"""

from uuid import UUID

from tumnis.core.events import EventEnvelope, subscribe
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.decisions import api
from tumnis.modules.decisions.payloads import DecisionMadeV1, DecisionUnavailablePayload

__all__ = ["DecisionMadeV1", "DecisionUnavailablePayload", "record_outcome"]

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
        await api.record_outcome(
            UUID(str(decision_id)),
            overridden=envelope.payload["decision"] not in ACCEPTED,
            final_value=payload.get("value"),
            outcome_at=envelope.occurred_at,
            session=session,
        )
