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

Both enrichment subscribers are direct (the relay runs them; each only reads and starts
its own workflow keyed on the event): every task write passes through them, and a queued
delivery each would add a workflow per `task.created` and per `task.updated` to the
events queue (a bulk import or the seed's thousands of tasks).

Subscriber names are part of every delivery's workflow id, so they never change.
"""

from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

from tumnis.core.clock import SystemClock
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
    "project_has_agent",
    "provision_project",
]

PROVISION_SUBSCRIBER: Final = "agents.provision_project"
REVIEW_SUBSCRIBER: Final = "agents.apply_review_decision"
ENRICH_CREATE_SUBSCRIBER: Final = "agents.enrich_on_create"
ENRICH_UPDATE_SUBSCRIBER: Final = "agents.enrich_on_update"

# How long a project seen without a provisioned agent is taken to still have none. The
# relay runs the enrichment subscribers for every task write, one after another, so a
# burst of writes in one project (quick adds, an import) asks once, not once per write.
# A task created in this window after provisioning completes is not enriched; the
# workflow re-checks every project it does start for.
NO_AGENT_TTL: Final = timedelta(seconds=5)
NO_AGENT_MAX: Final = 1024  # projects remembered at once; the oldest go first
_no_agent: dict[UUID, datetime] = {}  # project id -> when it was seen without an agent


def _now() -> datetime:
    return (api.enrichment_config().clock or SystemClock()).now()


async def project_has_agent(project_id: UUID, ctx: WorkspaceContext) -> bool:
    """Whether the project's agent is provisioned (`workflows.agent_provisioned`), with a
    project seen without one remembered for `NO_AGENT_TTL`."""
    now = _now()
    seen = _no_agent.get(project_id)
    if seen is not None and now - seen < NO_AGENT_TTL:
        return False
    if await workflows.agent_provisioned(project_id, ctx=ctx):
        _no_agent.pop(project_id, None)
        return True
    _no_agent.pop(project_id, None)
    while len(_no_agent) >= NO_AGENT_MAX:
        del _no_agent[next(iter(_no_agent))]
    _no_agent[project_id] = now
    return False


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


@subscribe("task.created", name=ENRICH_CREATE_SUBSCRIBER, direct=True)
async def enrich_on_create(envelope: EventEnvelope) -> None:
    payload = envelope.payload
    project_id = UUID(str(payload["project_id"]))
    ctx = WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)
    # No workflow at all for a project without a provisioned agent: bulk task writes
    # (imports, the seed) would otherwise queue one per task only to end at once.
    if not await project_has_agent(project_id, ctx):
        return
    await workflows.start_enrichment(
        envelope.workspace_id,
        UUID(str(payload["task_id"])),
        project_id,
        key=str(envelope.event_id),
    )


@subscribe("task.updated", name=ENRICH_UPDATE_SUBSCRIBER, direct=True)
async def enrich_on_update(envelope: EventEnvelope) -> None:
    if "label" not in (envelope.payload.get("changed_fields") or []):
        return
    task_id = UUID(str(envelope.payload["task_id"]))
    ctx = WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)
    project = (envelope.payload.get("doc") or {}).get("project_id")
    if project is not None and not await project_has_agent(UUID(str(project)), ctx):
        return  # nothing to enrich with: the task is not read at all
    async with tenant_session(ctx) as s:
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
