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

- `agents.enrich_on_create` (`task.created`, P1-08): starts `enrich_task` for the new task
  (workflow id `enrich:<task id>:<event id>`) when its project has a provisioned agent;
  the workflow itself decides whether the task needs anything.
- `agents.enrich_on_update` (`task.updated` naming `label`, P1-08): a task relabelled
  Human or Hybrid without an estimate, whose enrichment has ended, is enriched again: for
  the estimate alone after a finished enrichment, for everything missing after one that
  could not run. A task whose enrichment is still pending or running (it waits for the
  label itself), or never started, starts nothing, which keeps the enrichment's own label
  revision and Jev's label from starting a second one.

Subscriber names are part of every delivery's workflow id, so they never change.
"""

from typing import Final
from uuid import UUID

from tumnis.core.events import EventEnvelope, subscribe
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.core.versioning import NotFound

# The subscribers start `provision_profile` through api's starter seam, which workflows
# fills at import: loading the subscribers loads the workflow too, in every process.
from tumnis.modules.agents import api, workflows
from tumnis.modules.agents.rules import ESTIMATED_LABELS, enrichment_settled
from tumnis.modules.tasks import api as tasks

__all__ = [
    "ENRICH_CREATE_SUBSCRIBER",
    "ENRICH_UPDATE_SUBSCRIBER",
    "PROVISION_SUBSCRIBER",
    "REVIEW_SUBSCRIBER",
    "apply_review_decision",
    "enrich_on_create",
    "enrich_on_update",
    "provision_project",
]

PROVISION_SUBSCRIBER: Final = "agents.provision_project"
REVIEW_SUBSCRIBER: Final = "agents.apply_review_decision"
ENRICH_CREATE_SUBSCRIBER: Final = "agents.enrich_on_create"
ENRICH_UPDATE_SUBSCRIBER: Final = "agents.enrich_on_update"


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


@subscribe("task.created", name=ENRICH_CREATE_SUBSCRIBER)
async def enrich_on_create(envelope: EventEnvelope) -> None:
    payload = envelope.payload
    project_id = UUID(str(payload["project_id"]))
    ctx = WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)
    # No workflow at all for a project without a provisioned agent: bulk task writes
    # (imports, the seed) would otherwise queue one per task only to end at once.
    if not await workflows.agent_provisioned(project_id, ctx=ctx):
        return
    await workflows.start_enrichment(
        envelope.workspace_id,
        UUID(str(payload["task_id"])),
        project_id,
        key=str(envelope.event_id),
    )


@subscribe("task.updated", name=ENRICH_UPDATE_SUBSCRIBER)
async def enrich_on_update(envelope: EventEnvelope) -> None:
    if "label" not in (envelope.payload.get("changed_fields") or []):
        return
    task_id = UUID(str(envelope.payload["task_id"]))
    async with tenant_session(WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)) as s:
        try:
            task = await tasks.get_task(s, task_id)
        except NotFound:
            return
    if not enrichment_settled(task.enrichment_status):
        return
    if task.label is None or task.label.value not in ESTIMATED_LABELS:
        return
    if task.estimate_minutes is not None:
        return
    await workflows.start_enrichment(
        envelope.workspace_id,
        task_id,
        task.project_id,
        key=str(envelope.event_id),
        only=["estimate_minutes"] if task.enrichment_status == "done" else None,
    )
