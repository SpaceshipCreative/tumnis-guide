"""tasks event payload models and subscribers (P0-18, P1-13). The payload models live in
`payloads.py` (re-exported here) so `api.py` can emit them without importing this module.

Subscribers (never rename one: the name is part of every delivery's workflow ID):
- `tasks.create_default_columns`: on `project.created`, the project's six default board
  columns. Idempotent: the api writes them only while the project has none.
- `tasks.refresh_review_impact` (`task.created`), `tasks.refresh_review_impact_on_status`
  (`task.status_changed`) and `tasks.refresh_review_impact_on_update` (`task.updated`,
  when a field the impact reads changed): the blocking impact of the open review items
  of the task's project (P1-13, FR-6.1). Idempotent: it recomputes from the tasks.
- `tasks.apply_review_decision`: on `human.decided` for tasks' own kinds (P1-13, UX 2).
  `low_confidence_label`: accept sets the suggested label, edit the chosen one (source
  `user`); `estimate_outlier`: edit sets the estimate. Reject, snooze and accept on an
  estimate change nothing. Idempotent: a value already in place is not written again.
"""

from typing import Any, Final
from uuid import UUID

from tumnis.core.events import EventEnvelope, subscribe
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR, ActorRef
from tumnis.core.versioning import NotFound
from tumnis.modules.tasks import api
from tumnis.modules.tasks.payloads import (
    DOC_BODY_MAX_BYTES,
    HumanDecidedV1,
    ReviewItemAddedV1,
    TaskCreatedV1,
    TaskDoc,
    TaskStatusChangedV1,
    TaskUpdatedV1,
)
from tumnis.modules.tasks.review import ESTIMATE_KIND, LABEL_KIND

__all__ = [
    "DOC_BODY_MAX_BYTES",
    "HumanDecidedV1",
    "ReviewItemAddedV1",
    "TaskCreatedV1",
    "TaskDoc",
    "TaskStatusChangedV1",
    "TaskUpdatedV1",
    "apply_review_decision",
    "create_default_columns",
    "refresh_review_impact",
    "refresh_review_impact_on_status",
    "refresh_review_impact_on_update",
]

# The task fields `rules.downstream` reads: a change to any other leaves every impact as is.
IMPACT_FIELDS: Final = frozenset({"estimate_minutes", "label", "parent_id", "deleted", "status"})


def _system(envelope: EventEnvelope) -> WorkspaceContext:
    return WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)


@subscribe("project.created", name="tasks.create_default_columns")
async def create_default_columns(envelope: EventEnvelope) -> None:
    project_id = UUID(str(envelope.payload["project_id"]))
    async with tenant_session(_system(envelope)) as s:
        await api.ensure_default_columns(s, project_id)


@subscribe("task.created", name="tasks.refresh_review_impact")
async def refresh_review_impact(envelope: EventEnvelope) -> None:
    project_id = UUID(str(envelope.payload["project_id"]))
    async with tenant_session(_system(envelope)) as s:
        await api.refresh_review_impact(s, project_id)


@subscribe("task.status_changed", name="tasks.refresh_review_impact_on_status")
async def refresh_review_impact_on_status(envelope: EventEnvelope) -> None:
    async with tenant_session(_system(envelope)) as s:
        await api.refresh_review_impact_of_task(s, UUID(str(envelope.payload["task_id"])))


@subscribe("task.updated", name="tasks.refresh_review_impact_on_update")
async def refresh_review_impact_on_update(envelope: EventEnvelope) -> None:
    if not IMPACT_FIELDS.intersection(envelope.payload.get("changed_fields", ())):
        return
    async with tenant_session(_system(envelope)) as s:
        await api.refresh_review_impact_of_task(s, UUID(str(envelope.payload["task_id"])))


def _patch(kind: str, decision: str, item: Any, chosen: dict[str, Any]) -> dict[str, Any] | None:
    """The task fields a decision on one of tasks' kinds sets; None when it sets none."""
    if kind == LABEL_KIND and decision == "accept":
        return {"label": item.payload["suggested"]}
    if kind == LABEL_KIND and decision == "edit":
        return {"label": chosen["label"]}
    if kind == ESTIMATE_KIND and decision == "edit":
        return {"estimate_minutes": chosen["estimate_minutes"]}
    return None


@subscribe("human.decided", name="tasks.apply_review_decision")
async def apply_review_decision(envelope: EventEnvelope) -> None:
    event = envelope.payload
    if event.get("item_kind") not in {LABEL_KIND, ESTIMATE_KIND} or event.get("target_type") != (
        "task"
    ):
        return
    actor = ActorRef(envelope.actor) if envelope.actor.startswith("user:") else SYSTEM_ACTOR
    async with tenant_session(_system(envelope)) as s:
        item = await api.get_review_item(s, UUID(str(event["item_id"])))
        values = _patch(item.kind, str(event["decision"]), item, event.get("payload") or {})
        if values is None:
            return
        try:
            task = await api.get_task(s, item.target_id)
        except NotFound:
            return  # the task is gone: nothing to apply
        current = task.model_dump(mode="json")
        unchanged = all(current.get(field) == value for field, value in values.items())
        if unchanged and ("label" not in values or task.label_source == "user"):
            return
        await api.update_task(
            s,
            actor,
            task.id,
            api.TaskPatch.model_validate({**values, "version": task.version}),
            task.version,
            now=envelope.occurred_at,
            label_source="user" if "label" in values else None,
        )
