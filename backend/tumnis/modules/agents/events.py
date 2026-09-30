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
- `agents.apply_review_decision` listens to `human.decided`: accepting a
  `provisioning_failed` item provisions the project's profile again
  (`api.retry_provision`); accepting a `result` item moves its task to Done, and rejecting
  one adds the feedback as a comment, returns the task to In progress and runs the agent
  again (`api.request_run` with `rerun_of`), all as the person who decided, in one
  transaction. Idempotent: a second delivery finds the profile already provisioning, or the
  task no longer In review, and changes nothing. It is the one subscriber for agents'
  review kinds (Scott decision 29).

Subscriber names are part of every delivery's workflow id, so they never change.
"""

import logging
from typing import Final
from uuid import UUID

from tumnis.core.errors import ProblemError
from tumnis.core.events import EventEnvelope, subscribe
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR, ActorRef
from tumnis.core.versioning import NotFound
from tumnis.modules.agents import api

# The subscribers start `provision_profile` through api's starter seam, which workflows
# fills at import: loading the subscribers loads the workflow too, in every process.
from tumnis.modules.agents import workflows as _workflows
from tumnis.modules.agents.review_kinds import RESULT
from tumnis.modules.tasks import api as tasks

__all__ = [
    "DISPATCH_SUBSCRIBER",
    "PROVISION_SUBSCRIBER",
    "REVIEW_SUBSCRIBER",
    "SIGNAL_SUBSCRIBER",
    "apply_review_decision",
    "deliver_run_signal",
    "provision_project",
    "start_dispatch",
]

_log = logging.getLogger(__name__)

PROVISION_SUBSCRIBER: Final = "agents.provision_project"
REVIEW_SUBSCRIBER: Final = "agents.apply_review_decision"
DISPATCH_SUBSCRIBER: Final = "agents.start_dispatch"
SIGNAL_SUBSCRIBER: Final = "agents.deliver_run_signal"


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
    await _workflows.start_dispatch(
        envelope.workspace_id,
        UUID(str(payload["run_id"])),
        UUID(str(payload["project_id"])),
        None if priority is None else int(priority),
    )


@subscribe("run.signal", name=SIGNAL_SUBSCRIBER)
async def deliver_run_signal(envelope: EventEnvelope) -> None:
    payload = envelope.payload
    await _workflows.deliver_signal(
        envelope.workspace_id,
        UUID(str(payload["run_id"])),
        str(payload["kind"]),
        payload.get("reason"),
        key=str(envelope.event_id),
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
