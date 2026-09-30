"""agents event payload models and subscribers (P1-06, P1-13).

- `agents.provision_project` listens to `project.created`: it starts the project's
  `provision_profile` workflow (a new profile from the template, or a link to an existing
  one, as the payload's `profile` says; absent means create). The workflow id makes a
  redelivered event start nothing new.
- `agents.apply_review_decision` listens to `human.decided`: accepting a
  `provisioning_failed` item provisions the project's profile again
  (`api.retry_provision`); reject and snooze change nothing here. Idempotent: a second
  delivery finds the profile already provisioning, which starts nothing new. It is the one
  subscriber for agents' review kinds (Scott decision 29).

Subscriber names are part of every delivery's workflow id, so they never change.
"""

from typing import Final
from uuid import UUID

from tumnis.core.events import EventEnvelope, subscribe
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.agents import api

# The subscribers start `provision_profile` through api's starter seam, which workflows
# fills at import: loading the subscribers loads the workflow too, in every process.
from tumnis.modules.agents import workflows as _workflows  # noqa: F401

__all__ = [
    "PROVISION_SUBSCRIBER",
    "REVIEW_SUBSCRIBER",
    "apply_review_decision",
    "provision_project",
]

PROVISION_SUBSCRIBER: Final = "agents.provision_project"
REVIEW_SUBSCRIBER: Final = "agents.apply_review_decision"


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


@subscribe("human.decided", name=REVIEW_SUBSCRIBER)
async def apply_review_decision(envelope: EventEnvelope) -> None:
    payload = envelope.payload
    if payload.get("item_kind") != api.PROVISIONING_FAILED or payload.get("decision") != "accept":
        return
    ctx = WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)
    await api.retry_provision(UUID(str(payload["target_id"])), ctx=ctx)
