"""agents event payload models and subscribers.

Subscriber `agents.apply_review_decision` (P1-13; never rename it: the name is part of every
delivery's workflow ID): on `human.decided` for a `provisioning_failed` item, accept
retries the project's provisioning (`api.retry_provision`); reject and snooze change
nothing here. Idempotent: a second delivery finds the profile already provisioning.
"""

from uuid import UUID

from tumnis.core.events import EventEnvelope, subscribe
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.agents import api

__all__ = ["apply_review_decision"]


@subscribe("human.decided", name="agents.apply_review_decision")
async def apply_review_decision(envelope: EventEnvelope) -> None:
    payload = envelope.payload
    if payload.get("item_kind") != api.PROVISIONING_FAILED.kind or payload.get("decision") != (
        "accept"
    ):
        return
    project_id = UUID(str(payload["target_id"]))
    async with tenant_session(WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)) as s:
        await api.retry_provision(project_id, session=s)
