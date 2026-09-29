"""usage event payload models and subscribers.

One subscriber per event in the counter map (`rules.COUNTERS`), named
`usage.count_<event with dots as underscores>`: `task.created` is counted by
`usage.count_task_created`. A subscriber's name is part of every delivery's workflow ID,
so these names never change; an event that leaves the map keeps its subscriber until its
old deliveries are done.
"""

from tumnis.core.events import EventEnvelope, Handler, subscribe
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.usage import api
from tumnis.modules.usage.rules import COUNTERS


def subscriber_name(event: str) -> str:
    return f"usage.count_{event.replace('.', '_')}"


async def count(envelope: EventEnvelope) -> None:
    """Idempotent: a second delivery of the same event finds its ledger rows and adds
    nothing."""
    ctx = WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)
    async with tenant_session(ctx) as session:
        await api.record(session, envelope)


def _register() -> dict[str, Handler]:
    return {event: subscribe(event, name=subscriber_name(event))(count) for event in COUNTERS}


SUBSCRIBERS = _register()
