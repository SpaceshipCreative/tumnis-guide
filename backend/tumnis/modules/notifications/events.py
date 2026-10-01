"""notifications event payload models and subscribers (P4-05 on P2-16's seam, FR-8.3,
FR-8.4). notifications emits no events of its own.

Subscribers (each idempotent: the notification row's dedupe key is the event id, and its
push's workflow id is the row's id):
- `review_item.added` -> `notifications.push_review_item`: every review item reaches the
  human (questions, approvals and results all arrive as review items), now or batched by
  `rules.delivery_decision`;
- `focus.event` -> `notifications.push_focus_event`: likewise every focus event; the
  day's end (`day_end`) is also a natural break, so it releases what Quiet held;
- `task.status_changed` leaving In progress -> `notifications.flush_on_break` and
  `focus.level_changed` -> `notifications.flush_on_level`: release what Quiet held once
  `rules.flush_due` says the break came, or the level no longer batches.
"""

from typing import Final
from uuid import UUID

from tumnis.core.events import EventEnvelope, subscribe
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.notifications import api, workflows

__all__ = ["flush_on_break", "flush_on_level", "push_focus_event", "push_review_item"]

IN_PROGRESS: Final = "in_progress"
DAY_END: Final = "day_end"


def _ctx(envelope: EventEnvelope) -> WorkspaceContext:
    return WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)


async def _push_now(envelope: EventEnvelope, made: api.Recorded) -> None:
    if made.decision == "now":
        await workflows.start_push(envelope.workspace_id, made.id)


async def _flush(envelope: EventEnvelope, *, day_ended: bool = False) -> None:
    released = await api.flush(
        _ctx(envelope),
        dedupe_key=f"flush:{envelope.event_id}",
        now=envelope.occurred_at,
        day_ended=day_ended,
    )
    if released is not None:
        await workflows.start_push(envelope.workspace_id, released)


@subscribe("review_item.added", name="notifications.push_review_item")
async def push_review_item(envelope: EventEnvelope) -> None:
    payload = envelope.payload
    project = payload.get("project_id")
    made = await api.record_review_item(
        _ctx(envelope),
        item_id=UUID(str(payload["item_id"])),
        kind=str(payload["kind"]),
        project_id=None if project is None else UUID(str(project)),
        dedupe_key=str(envelope.event_id),
        now=envelope.occurred_at,
    )
    await _push_now(envelope, made)


@subscribe("focus.event", name="notifications.push_focus_event")
async def push_focus_event(envelope: EventEnvelope) -> None:
    payload = envelope.payload
    made = await api.record_focus_event(
        _ctx(envelope),
        event_id=UUID(str(payload["event_id"])),
        kind=str(payload["kind"]),
        dedupe_key=str(envelope.event_id),
        now=envelope.occurred_at,
    )
    await _push_now(envelope, made)
    if payload.get("kind") == DAY_END:
        await _flush(envelope, day_ended=True)


@subscribe("task.status_changed", name="notifications.flush_on_break")
async def flush_on_break(envelope: EventEnvelope) -> None:
    if envelope.payload.get("from") == IN_PROGRESS:
        await _flush(envelope)


@subscribe("focus.level_changed", name="notifications.flush_on_level")
async def flush_on_level(envelope: EventEnvelope) -> None:
    await _flush(envelope)
