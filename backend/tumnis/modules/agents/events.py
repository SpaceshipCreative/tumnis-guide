"""agents event payload models and subscribers (P1-06, P1-13, P2-04).

- `agents.provision_project` listens to `project.created`: it starts the project's
  `provision_profile` workflow (a new profile from the template, or a link to an existing
  one, as the payload's `profile` says; absent means create). The workflow id makes a
  redelivered event start nothing new.
- `agents.start_dispatch` listens to `run.requested` (P2-04, R-23): it enqueues the run's
  `dispatch_run` workflow on the `runs` queue, partitioned by project; the workflow id is
  the run id, so a redelivered event starts nothing new.
- `agents.deliver_run_signal` listens to `run.signal` (P2-04): it sends the signal to the
  run's workflow with the event id as the idempotency key, so a redelivery sends nothing
  twice.
- `agents.cancel_paused_runs` listens to `agents.paused` (P2-09, SAF-4): in one
  transaction, `run.signal{cancel}` (reason `killswitch`, or `project_paused`) for each
  running or waiting `dispatch_run` run in the pause's scope; each run's workflow stops
  its agent through the adapter and ends it `cancelled`.
- `agents.release_held_runs` listens to `agents.resumed` (P2-09): sends `release` to each
  held run in scope that no other pause holds (send idempotency key `<event id>:<run>`),
  and its workflow goes on.
- `agents.apply_review_decision` listens to `human.decided`: accepting a
  `provisioning_failed` item provisions the project's profile again
  (`api.retry_provision`); accepting a `result` item moves its task to Done, and rejecting
  one adds the feedback as a comment, returns the task to In progress and runs the agent
  again (`api.request_run` with `rerun_of`), all as the person who decided, in one
  transaction. Idempotent: a second delivery finds the profile already provisioning, or the
  task no longer In review, and changes nothing. It is the one subscriber for agents'
  review kinds (Scott decision 29). P2-05: a `question` answered or an `approval`
  approved or denied wakes the wait's flow (`human_flows.deliver_human_decision`; the
  row already holds the decision), resuming it on this application version after a
  deploy. Human answers reach waiting workflows only this way, never from the request.
- `agents.start_question_flow` (`question.asked`) and `agents.start_approval_flow`
  (`approval.requested`, P2-05): start the wait's flow on the `human` queue (workflow id
  `<kind>:<id>:<app version>`, so a redelivery starts nothing new).

- `agents.enrich_on_create` (`task.created`, P1-08): starts `enrich_task` for the new task
  (workflow id `enrich:<task id>:<event id>`) when its project has a provisioned agent;
  the workflow itself decides whether the task needs anything.
- `agents.enrich_on_update` (`task.updated` naming `label`, P1-08): a task relabelled
  Human or Hybrid without an estimate, whose enrichment has ended, is enriched again: for
  the estimate alone after a finished enrichment, for everything missing after one that
  could not run. A task whose enrichment is still pending or running (it waits for the
  label itself), or never started, starts nothing, which keeps the enrichment's own label
  revision and Jev's label from starting a second one; an enrichment that ends without
  the estimate its task now needs follows up with an estimate-only one itself
  (`rules.estimate_follow_up`).

Both enrichment subscribers are direct (the relay runs them; each only reads and starts
its own workflow keyed on the event): every task write passes through them, and a queued
delivery each would add a workflow per `task.created` and per `task.updated` to the
events queue (a bulk import or the seed's thousands of tasks).

Subscriber names are part of every delivery's workflow id, so they never change.
"""

import logging
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

from tumnis.core.clock import SystemClock
from tumnis.core.errors import ProblemError
from tumnis.core.events import EventEnvelope, subscribe
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR, ActorRef
from tumnis.core.versioning import NotFound

# The subscribers start `provision_profile` through api's starter seam, which workflows
# fills at import: loading the subscribers loads the workflow too, in every process.
from tumnis.modules.agents import api, human_flows, workflows
from tumnis.modules.agents.review_kinds import APPROVAL, QUESTION, RESULT
from tumnis.modules.agents.rules import ESTIMATED_LABELS, enrichment_settled
from tumnis.modules.tasks import api as tasks

__all__ = [
    "APPROVAL_SUBSCRIBER",
    "CANCEL_PAUSED_SUBSCRIBER",
    "DISPATCH_SUBSCRIBER",
    "ENRICH_CREATE_SUBSCRIBER",
    "ENRICH_UPDATE_SUBSCRIBER",
    "PROVISION_SUBSCRIBER",
    "QUESTION_SUBSCRIBER",
    "RELEASE_HELD_SUBSCRIBER",
    "REVIEW_SUBSCRIBER",
    "SIGNAL_SUBSCRIBER",
    "apply_review_decision",
    "cancel_paused_runs",
    "deliver_run_signal",
    "enrich_on_create",
    "enrich_on_update",
    "project_has_agent",
    "provision_project",
    "release_held_runs",
    "start_approval_flow",
    "start_dispatch",
    "start_question_flow",
]

_log = logging.getLogger(__name__)

PROVISION_SUBSCRIBER: Final = "agents.provision_project"
REVIEW_SUBSCRIBER: Final = "agents.apply_review_decision"
DISPATCH_SUBSCRIBER: Final = "agents.start_dispatch"
SIGNAL_SUBSCRIBER: Final = "agents.deliver_run_signal"
ENRICH_CREATE_SUBSCRIBER: Final = "agents.enrich_on_create"
ENRICH_UPDATE_SUBSCRIBER: Final = "agents.enrich_on_update"
CANCEL_PAUSED_SUBSCRIBER: Final = "agents.cancel_paused_runs"
RELEASE_HELD_SUBSCRIBER: Final = "agents.release_held_runs"
QUESTION_SUBSCRIBER: Final = "agents.start_question_flow"
APPROVAL_SUBSCRIBER: Final = "agents.start_approval_flow"

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


@subscribe("run.requested", name=DISPATCH_SUBSCRIBER)
async def start_dispatch(envelope: EventEnvelope) -> None:
    payload = envelope.payload
    priority = payload.get("priority")
    await workflows.start_dispatch(
        envelope.workspace_id,
        UUID(str(payload["run_id"])),
        UUID(str(payload["project_id"])),
        None if priority is None else int(priority),
    )


@subscribe("run.signal", name=SIGNAL_SUBSCRIBER)
async def deliver_run_signal(envelope: EventEnvelope) -> None:
    payload = envelope.payload
    await workflows.deliver_signal(
        envelope.workspace_id,
        UUID(str(payload["run_id"])),
        str(payload["kind"]),
        payload.get("reason"),
        key=str(envelope.event_id),
    )


@subscribe("agents.paused", name=CANCEL_PAUSED_SUBSCRIBER)
async def cancel_paused_runs(envelope: EventEnvelope) -> None:
    payload = envelope.payload
    scope = str(payload["scope"])
    project = payload.get("project_id")
    project_id = None if project is None else UUID(str(project))
    ctx = WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)
    async with tenant_session(ctx) as s:
        for run_id in await api.runs_to_cancel(s, scope, project_id):
            await api.signal_run(
                ctx,
                run_id,
                "cancel",
                reason=api.CANCEL_REASON[scope],
                session=s,
                now=envelope.occurred_at,
            )


@subscribe("agents.resumed", name=RELEASE_HELD_SUBSCRIBER)
async def release_held_runs(envelope: EventEnvelope) -> None:
    payload = envelope.payload
    project = payload.get("project_id")
    project_id = None if project is None else UUID(str(project))
    ctx = WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)
    async with tenant_session(ctx) as s:
        free = await api.held_runs_free(s, str(payload["scope"]), project_id)
    for run_id in free:
        await workflows.release_held(
            envelope.workspace_id, run_id, key=f"{envelope.event_id}:{run_id}"
        )


@subscribe("human.decided", name=REVIEW_SUBSCRIBER)
async def apply_review_decision(envelope: EventEnvelope) -> None:
    payload = envelope.payload
    kind = payload.get("item_kind")
    if kind == api.PROVISIONING_FAILED and payload.get("decision") == "accept":
        ctx = WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)
        await api.retry_provision(UUID(str(payload["target_id"])), ctx=ctx)
    elif kind == RESULT and payload.get("target_type") == "task":
        await _apply_result_decision(envelope)
    elif kind in (QUESTION, APPROVAL) and payload.get("decision") != "snooze":
        await human_flows.deliver_human_decision(
            envelope.workspace_id,
            str(kind),
            UUID(str(payload["item_id"])),
            str(payload["decision"]),
            key=str(envelope.event_id),
        )


@subscribe("question.asked", name=QUESTION_SUBSCRIBER)
async def start_question_flow(envelope: EventEnvelope) -> None:
    await human_flows.start_human_wait(
        envelope.workspace_id, QUESTION, UUID(str(envelope.payload["question_id"]))
    )


@subscribe("approval.requested", name=APPROVAL_SUBSCRIBER)
async def start_approval_flow(envelope: EventEnvelope) -> None:
    await human_flows.start_human_wait(
        envelope.workspace_id, APPROVAL, UUID(str(envelope.payload["approval_id"]))
    )


async def _apply_result_decision(envelope: EventEnvelope) -> None:
    """Accept: In review -> Done. Reject: the feedback as a comment, In review -> In
    progress, and a new run of the task (`rerun_of` the result's run), whose packet carries
    the comment. Both are human edges, taken as the person who decided."""
    payload = envelope.payload
    decision = payload.get("decision")
    if decision not in ("accept", "reject"):
        return
    actor = ActorRef(envelope.actor)
    ctx = WorkspaceContext(envelope.workspace_id, actor)
    async with tenant_session(ctx) as s:
        item = await tasks.get_review_item(s, UUID(str(payload["item_id"])))
        try:
            task = await tasks.get_task(s, item.target_id)
        except NotFound:
            return  # the task is gone: nothing to apply
        if task.status != tasks.Status.IN_REVIEW:
            return  # applied already, or the human moved the task another way
        if decision == "accept":
            await tasks.change_status(
                s, actor, task.id, tasks.Status.DONE, task.version, now=envelope.occurred_at
            )
            return
        feedback = str((payload.get("payload") or {}).get("feedback") or "")
        await tasks.add_comment(s, actor, task.id, feedback, now=envelope.occurred_at)
        task = await tasks.get_task(s, task.id)
        await tasks.change_status(
            s, actor, task.id, tasks.Status.IN_PROGRESS, task.version, now=envelope.occurred_at
        )
        rerun_of = item.payload.get("run_id")
        try:
            async with s.begin_nested():
                await api.request_run(
                    task.id,
                    api.RunKind.TASK,
                    rerun_of=None if rerun_of is None else UUID(str(rerun_of)),
                    ctx=ctx,
                    session=s,
                    now=envelope.occurred_at,
                )
        except ProblemError as exc:
            if exc.code == "run_already_active":
                raise  # the rejected run has not ended yet: the delivery is retried
            # The task can no longer run (its label or its project's agent changed): the
            # rejection, its comment and the move back to In progress stand; the human
            # runs it again when it can.
            _log.warning("no rerun after a rejected result: %s", exc.code)


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
