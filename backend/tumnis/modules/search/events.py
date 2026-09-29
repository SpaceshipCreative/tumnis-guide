"""search subscribers (P0-20, FR-3.9): task and project events feed the index.

Search calls no other module (boundary rule 4), so the events carry what it indexes: the
task's `doc` on `task.created` and `task.updated`, the name and goal on `project.created`
and `project.updated`. `project.archived` hides the project row; the unarchive
(`project.updated` with `archived: false`) brings it back. A failing subscriber is retried
and dead-lettered on its own and never holds up the write that emitted the event.

`task.status_changed` carries no doc (P0-18 fixed its payload), so it changes nothing
here: bumping the row's time on it would let a late text change lose to the status change.

One subscriber per event, named `search.index_<event with dots as underscores>`. A
subscriber's name is part of every delivery's workflow ID, so these names never change.
"""

from typing import Final

from tumnis.core.events import EventEnvelope, Handler, subscribe
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.search import api

INDEXED_EVENTS: Final = (
    "task.created",
    "task.updated",
    "project.created",
    "project.updated",
    "project.archived",
)


def subscriber_name(event: str) -> str:
    return f"search.index_{event.replace('.', '_')}"


async def index(envelope: EventEnvelope) -> None:
    """Idempotent: a second delivery writes the same row, a late one changes nothing."""
    async with tenant_session(WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)) as s:
        await api.index_event(s, envelope)


def _register() -> dict[str, Handler]:
    return {event: subscribe(event, name=subscriber_name(event))(index) for event in INDEXED_EVENTS}


SUBSCRIBERS = _register()
