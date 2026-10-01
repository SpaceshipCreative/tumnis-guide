"""focus event payload models and subscribers (P2-15).

Payloads (`payloads.py`): `focus.event`, `focus.responded`, `focus.level_changed`.

Subscribers (each idempotent: a redelivery starts no second workflow and sends no second
message, since workflow ids and message keys come from the event):
- `plan.published` -> `focus.start_plan`: the plan's `focus_plan` starts at the plan's
  publication, and the day's older plans' workflows hear `superseded`.
- `task.status_changed` -> `focus.track_session`: a task leaving In progress ends its
  session (`end`); a task entering it starts one at Nudge or above (another task's open
  session ends, and `switched` fires at Coach and above).
- `run.started` and `artifact.updated` -> `focus.agent_activity` / `focus.git_activity`:
  agent or git activity on a task (FR-10.7b) suppresses its next check-in.
- `focus.responded` -> `focus.wake_on_response` and `focus.level_changed` ->
  `focus.wake_on_level`: the open sessions look at their next check-in again.

`calendar.synced` re-deriving a published plan's events is not subscribed: nothing on main
moves a published plan's blocks on a sync, so there is nothing to re-derive yet.
"""

from datetime import date
from typing import Final
from uuid import UUID

from tumnis.core.events import EventEnvelope, subscribe
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.focus import api, workflows
from tumnis.modules.focus.payloads import FocusEventV1, FocusLevelChangedV1, FocusRespondedV1
from tumnis.modules.integrations import api as integrations

__all__ = [
    "FocusEventV1",
    "FocusLevelChangedV1",
    "FocusRespondedV1",
    "agent_activity",
    "git_activity",
    "start_plan",
    "track_session",
    "wake_on_level",
    "wake_on_response",
]

IN_PROGRESS: Final = "in_progress"


def _ctx(envelope: EventEnvelope) -> WorkspaceContext:
    return WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)


def _uuid(value: object) -> UUID | None:
    return None if value is None else UUID(str(value))


@subscribe("plan.published", name="focus.start_plan")
async def start_plan(envelope: EventEnvelope) -> None:
    payload = envelope.payload
    await workflows.start_plan(
        envelope.workspace_id,
        UUID(str(payload["plan_id"])),
        date.fromisoformat(str(payload["day"])),
        envelope.occurred_at,
    )


@subscribe("task.status_changed", name="focus.track_session")
async def track_session(envelope: EventEnvelope) -> None:
    payload = envelope.payload
    task_id = UUID(str(payload["task_id"]))
    ctx = _ctx(envelope)
    at = envelope.occurred_at
    end = {"kind": "end", "at": at.isoformat()}
    key = f"{envelope.event_id}:end"
    if payload.get("from") == IN_PROGRESS:
        for workflow_id in await api.end_sessions(ctx, task_id, at):
            await workflows.send(workflow_id, end, key)
    if payload.get("to") != IN_PROGRESS:
        return
    started = await api.start_session(ctx, task_id, at)
    if started is None:
        return
    for workflow_id in started.ended_workflows:
        await workflows.send(workflow_id, end, key)
    await workflows.start_session(envelope.workspace_id, started)


@subscribe("run.started", name="focus.agent_activity")
async def agent_activity(envelope: EventEnvelope) -> None:
    task_id = _uuid(envelope.payload.get("task_id"))
    if task_id is not None:
        await api.record_activity(_ctx(envelope), [task_id], envelope.occurred_at)


@subscribe("artifact.updated", name="focus.git_activity")
async def git_activity(envelope: EventEnvelope) -> None:
    artifact_id = _uuid(envelope.payload.get("artifact_id"))
    if artifact_id is None:
        return
    ctx = _ctx(envelope)
    owners = await integrations.context_owners(
        ctx, target_type="artifact", target_id=artifact_id, owner_type="task"
    )
    await api.record_activity(ctx, owners, envelope.occurred_at)


@subscribe("focus.responded", name="focus.wake_on_response")
async def wake_on_response(envelope: EventEnvelope) -> None:
    task_id = _uuid(envelope.payload.get("task_id"))
    if task_id is None:
        return
    message = {"kind": "response", "at": envelope.occurred_at.isoformat()}
    for workflow_id in await api.open_session_workflows(_ctx(envelope), task_id):
        await workflows.send(workflow_id, message, str(envelope.event_id))


@subscribe("focus.level_changed", name="focus.wake_on_level")
async def wake_on_level(envelope: EventEnvelope) -> None:
    message = {"kind": "wake", "at": envelope.occurred_at.isoformat()}
    for workflow_id in await api.open_session_workflows(_ctx(envelope)):
        await workflows.send(workflow_id, message, str(envelope.event_id))
