"""agents event payload models and subscribers.

Digest subscribers (P2-03, FR-13.1): one per event the digests carry
(`rules.DIGEST_EVENTS`), named `agents.digest_<event with dots as underscores>`. Each
turns the event into its digest entries (`api.record_event`); a redelivered event adds
nothing (one entry per event and kind). A subscriber's name is part of every delivery's
workflow ID, so these names never change. They subscribe by name: `document.*` is
P1-16's payload and `focus.*` P2-15's, and the entries read the payload as data.
"""

from tumnis.core.events import EventEnvelope, Handler, subscribe
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.agents import api
from tumnis.modules.agents.rules import DIGEST_EVENTS


def digest_subscriber_name(event: str) -> str:
    return f"agents.digest_{event.replace('.', '_')}"


async def record_digest_entries(envelope: EventEnvelope) -> None:
    """Idempotent: an entry already there for this event and kind is left alone."""
    ctx = WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)
    async with tenant_session(ctx) as s:
        await api.record_event(
            s,
            name=envelope.name,
            payload=envelope.payload,
            actor=envelope.actor,
            event_id=envelope.event_id,
            occurred_at=envelope.occurred_at,
        )


def _register(event: str) -> Handler:
    async def handler(envelope: EventEnvelope) -> None:
        await record_digest_entries(envelope)

    handler.__name__ = digest_subscriber_name(event).partition(".")[2]
    return subscribe(event, name=digest_subscriber_name(event))(handler)


DIGEST_SUBSCRIBERS = {event: _register(event) for event in DIGEST_EVENTS}
