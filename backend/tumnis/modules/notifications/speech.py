"""Spoken focus messages (P4-03, FR-10.8, FR-11.7): the worker side of voice mode.

Subscriber (idempotent: the clip is keyed on the focus event's id, so a redelivery
replaces it rather than adding a second):
- `focus.event` -> `notifications.speak_focus_event`: with voice on for the event's level
  and the server engine chosen, the Speech slot makes a clip of exactly the message's
  in-app text (`decisions.speak`). The focus bar reads `speak` and the clip's id from
  `GET /v1/focus/current`.

A synthesis failure never retries or dead-letters: the message simply has no clip, and the
PWA speaks the same text with the browser's own voice (the plan's Piper-down fallback).
Only field names reach the log, never the text.

It lives in its own file, registered by `events.py`, so browser push (P4-05) and delivery
(P2-16) can change `events.py` and `workflows.py` without touching it.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import structlog

from tumnis.core.adapters.errors import AdapterError
from tumnis.core.events import EventEnvelope, subscribe
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.core.versioning import NotFound
from tumnis.modules.decisions import api as decisions
from tumnis.modules.tasks import api as tasks

__all__ = ["speak_focus_event", "speak_message"]

_log = structlog.get_logger(__name__)


@subscribe("focus.event", name="notifications.speak_focus_event")
async def speak_focus_event(envelope: EventEnvelope) -> None:
    await speak_message(
        WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR),
        envelope.payload,
        at=envelope.occurred_at,
    )


async def speak_message(
    ctx: WorkspaceContext, payload: dict[str, Any], *, at: datetime
) -> decisions.Clip | None:
    """The focus event's clip, when voice is on for its level and the server engine makes
    it; None otherwise, or when the engine failed (logged by field name)."""
    voice = await decisions.voice_settings(ctx)
    if payload.get("level") not in voice.enabled_levels or voice.engine != "server":
        return None
    task_id = payload.get("task_id")
    project_id = None if task_id is None else await _project_of(ctx, UUID(str(task_id)))
    try:
        return await decisions.speak(
            ctx,
            str(payload["message"]),
            message_id=UUID(str(payload["event_id"])),
            now=at,
            project_id=project_id,
        )
    except AdapterError as exc:
        _log.warning(
            "notifications.speak_failed",
            adapter=exc.adapter,
            op=exc.op,
            retryable=exc.retryable,
            event_id=str(payload["event_id"]),
        )
        return None


async def _project_of(ctx: WorkspaceContext, task_id: UUID) -> UUID | None:
    async with tenant_session(ctx) as s:
        try:
            return (await tasks.get_task(s, task_id)).project_id
        except NotFound:
            return None
