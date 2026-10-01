"""planning event payload models and subscribers.

- `plan.published` (P1-11): `payloads.PlanPublishedV1`, emitted by `api.publish_plan`.
- `calendar.synced` -> `planning.drop_day_calendars`: a finished sync may have changed any
  day's events, so every cached day calendar of the workspace is dropped (P1-10). The
  drop is idempotent: running it again drops nothing new.
- `human.decided` -> `planning.apply_plan_issue` (P1-11, J6): a `plan_issue` item decided
  in the review queue takes its offer (accept: the split; edit: the move; reject: the task
  stays off today). Idempotent: an issue already resolved (a redelivery, or the Today
  panel's own split or move, which decides the item itself) is left alone.

Nothing here re-plans: no task, calendar or settings event starts `build_plan`.
"""

from typing import Final
from uuid import UUID

from tumnis.core.cache import invalidate_on_commit
from tumnis.core.events import EventEnvelope, subscribe
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR, ActorRef
from tumnis.modules.planning import api
from tumnis.modules.planning.payloads import PlanPublishedV1

__all__ = ["PLAN_ISSUE_SUBSCRIBER", "PlanPublishedV1", "apply_plan_issue", "drop_day_calendars"]

PLAN_ISSUE_SUBSCRIBER: Final = "planning.apply_plan_issue"


@subscribe("calendar.synced", name="planning.drop_day_calendars")
async def drop_day_calendars(envelope: EventEnvelope) -> None:
    async with tenant_session(WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)) as s:
        await invalidate_on_commit(s, tag=api.free_blocks_tag(envelope.workspace_id))


@subscribe("human.decided", name=PLAN_ISSUE_SUBSCRIBER)
async def apply_plan_issue(envelope: EventEnvelope) -> None:
    payload = envelope.payload
    if payload.get("item_kind") != api.PLAN_ISSUE:
        return
    ctx = WorkspaceContext(envelope.workspace_id, ActorRef(envelope.actor or SYSTEM_ACTOR))
    await api.apply_issue_decision(
        ctx, UUID(str(payload["item_id"])), str(payload.get("decision")), now=envelope.occurred_at
    )
