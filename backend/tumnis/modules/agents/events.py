"""agents event payload models and subscribers (P1-06).

- `agents.provision_project` listens to `project.created`: it starts the project's
  `provision_profile` workflow (a new profile from the template, or a link to an existing
  one, as the payload's `profile` says; absent means create). The workflow id and the
  deduplication id make a redelivered event start nothing new.
- `agents.retry_provisioning` listens to `human.decided`: accepting a `provisioning_failed`
  item provisions the project's profile again.

Subscriber names are part of every delivery's workflow id, so they never change.
"""

from typing import Final
from uuid import UUID

from tumnis.core.events import EventEnvelope, subscribe
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.agents import api

__all__ = ["PROVISION_SUBSCRIBER", "RETRY_SUBSCRIBER", "provision_project", "retry_provisioning"]

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
        event_id=envelope.event_id,
    )


@subscribe("human.decided", name=RETRY_SUBSCRIBER)
async def retry_provisioning(envelope: EventEnvelope) -> None:
    payload = envelope.payload
    if payload.get("item_kind") != api.PROVISIONING_FAILED or payload.get("decision") != "accept":
        return
    ctx = WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)
    await api.retry_provision(UUID(str(payload["target_id"])), ctx=ctx)
