"""agents event payload models and subscribers.

Provisioning (P1-06):

- `agents.provision_project` listens to `project.created`: it starts the project's
  `provision_profile` workflow (a new profile from the template, or a link to an existing
  one, as the payload's `profile` says; absent means create). The workflow id makes a
  redelivered event start nothing new.
- `agents.retry_provisioning` listens to `human.decided`: accepting a `provisioning_failed`
  item provisions the project's profile again.

Digests (P2-03, FR-13.1): one subscriber per event the digests carry
(`rules.DIGEST_EVENTS`), named `agents.digest_<event with dots as underscores>`. Each
turns the event into its digest entries (`api.record_event`); a redelivered event adds
nothing (one entry per event and kind). They subscribe by name: `document.*` is P1-16's
payload and `focus.*` P2-15's, and the entries read the payload as data.

Subscriber names are part of every delivery's workflow id, so they never change.
"""

from typing import Final
from uuid import UUID

from tumnis.core.events import EventEnvelope, Handler, subscribe
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.agents import api

# The subscribers start `provision_profile` through api's starter seam, which workflows
# fills at import: loading the subscribers loads the workflow too, in every process.
from tumnis.modules.agents import workflows as _workflows  # noqa: F401
from tumnis.modules.agents.rules import DIGEST_EVENTS

__all__ = [
    "DIGEST_SUBSCRIBERS",
    "PROVISION_SUBSCRIBER",
    "RETRY_SUBSCRIBER",
    "digest_subscriber_name",
    "provision_project",
    "record_digest_entries",
    "retry_provisioning",
]

PROVISION_SUBSCRIBER: Final = "agents.provision_project"
RETRY_SUBSCRIBER: Final = "agents.retry_provisioning"


@subscribe("project.created", name=PROVISION_SUBSCRIBER)
async def provision_project(envelope: EventEnvelope) -> None:
    payload = envelope.payload
    choice = payload.get("profile") or {}
    mode: api.ProvisionMode = "link" if choice.get("mode") == "link" else "create"
    await api.provision_project(
        envelope.workspace_id,
        UUID(str(payload["project_id"])),
        mode=mode,
        link_name=choice.get("name") if mode == "link" else None,
    )


@subscribe("human.decided", name=RETRY_SUBSCRIBER)
async def retry_provisioning(envelope: EventEnvelope) -> None:
    payload = envelope.payload
    if payload.get("item_kind") != api.PROVISIONING_FAILED or payload.get("decision") != "accept":
        return
    ctx = WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)
    await api.retry_provision(UUID(str(payload["target_id"])), ctx=ctx)


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
