"""agents DBOS workflows and steps (P1-04, FR-5.11, FR-14.6, A9).

- `run_skill(workspace_id, packet)`: the phase 1 run (reused by P1-08 and P1-11).
  `dispatch_step` writes the run through the daemon transport (runs row, mailbox row,
  NOTIFY); the workflow body waits on `DBOS.recv_async(topic="run:<run id>")` (never in a
  step, R-30) for the result the api hands over; `finish_step` records the outcome. The
  workflow ID is `run_skill:<run id>`, so starting it twice runs it once.
- `runner_sweep(scheduled_at, context)`: scheduled `* * * * *`; marks runners offline after
  three missed heartbeats (`rules.runner_status` at the scheduled time) and ends the runs
  waiting on them `runner_lost`, telling each waiting workflow through `DBOS.send`.
- `check_profile_health(workspace_id, profile_id, request_id)`: asks the runner (or the
  endpoint) and writes the profile's `health`. P2-10: the runner's check carries the repos
  and apps the profile's tokens must and must not reach; its report's MCP servers are
  matched against the project's allowlist and its token reach judged, and drift or foreign
  reach marks the profile degraded with one `drift` review item.
- `provision_profile(workspace_id, project_id, mode, link_name, attempt)` (P1-06): gives a
  project its Hermes profile (created from the template or linked) exactly once; workflow
  id `provision:<project id>` (retry n: `provision:<project id>:<n>`), the runner's answer
  on topic `provision:<project id>`.
- `profile_health_sweep(scheduled_at, context)`: scheduled every 15 minutes; enqueues
  `check_profile_health` for every live, unpaused profile.
"""

import asyncio
import contextvars
import hashlib
import json
import logging
import statistics
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID, uuid5

from dbos import DBOS, SetEnqueueOptions, SetWorkflowID
from dbos._error import DBOSNonExistentWorkflowError  # dbos 3.1.0: not re-exported
from pydantic import BaseModel, ValidationError
from sqlalchemy import Table, Text, cast, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import audit, db, faults
from tumnis.core.clock import SystemClock
from tumnis.core.limits import WAIT_SLICE_S
from tumnis.core.live import mark_changed
from tumnis.core.outbox import emit
from tumnis.core.schemas import registry
from tumnis.core.tenancy import WorkspaceContext, tenant_session, use_workspace
from tumnis.core.types import SYSTEM_ACTOR, ActorRef
from tumnis.core.versioning import NotFound, StaleVersion
from tumnis.modules.agents import api

# P2-18: importing archive also registers its steps with the project archive workflows.
from tumnis.modules.agents import archive as _archive
from tumnis.modules.agents.adapters.hermes import DaemonTransport, McpEndpointTransport
from tumnis.modules.agents.models import (
    AgentProfile,
    RunEventRow,
    Runner,
    RunnerMessage,
    RunRow,
)
from tumnis.modules.agents.packet_builder import (
    MCP_PATH,
    REST_BASE,
    Callback,
    TaskPacket,
    build_packet,
    enrichment_request,
    render_prompt,
)
from tumnis.modules.agents.payloads import RunSignalV1, RunStartedV1
from tumnis.modules.agents.protocol import Provision, ProvisionResult, SchemaRef
from tumnis.modules.agents.review_kinds import DRIFT, DriftPayload, ForeignReach
from tumnis.modules.agents.rules import (
    ESTIMATE,
    MASTER_PROFILE_NAME,
    RUN_TRANSITIONS,
    TERMINAL_STATUSES,
    DispatchProfile,
    DispatchTask,
    Drift,
    InvalidProfileName,
    ReachTargets,
    ReachVerdict,
    RunKind,
    RunStatus,
    TaskSnapshot,
    allowlist_drift,
    can_dispatch,
    enrichment_errors,
    estimate_follow_up,
    merge_enrichment,
    missing_fields,
    needs_enrichment,
    plausibility_flag,
    profile_health,
    profile_name_for,
    provision_outcome,
    reach_targets,
    reach_verdict,
    run_transition,
    runner_status,
    validate_profile_name,
)
from tumnis.modules.agents.skill_io import ESTIMATE_RANGE, EnrichmentRequest, EnrichmentResult
from tumnis.modules.auth import api as auth
from tumnis.modules.coolify import api as coolify
from tumnis.modules.decisions import api as decisions
from tumnis.modules.decisions import generation_api
from tumnis.modules.projects import api as projects
from tumnis.modules.tasks import api as tasks

if TYPE_CHECKING:
    from dbos import DBOSClient, WorkflowHandleAsync

_log = logging.getLogger(__name__)

RECV_GRACE_S: Final = 30  # the run's timeout plus this, then timed_out (plan default)
HEALTH_TIMEOUT_S: Final = 30
RUNS_PARTITION_CONCURRENCY: Final = 2  # runs at once per partition (plan default, SAF-5)
RUNS_QUEUE_POLL_S: Final = 0.5  # a freed slot is taken within half a second
RUNNER_SWEEP_SCHEDULE: Final = "* * * * *"
RUNNER_SWEEP_NAME: Final = "runner-sweep"
# Its own queue, not maintenance: a long audit or housekeeping run must not delay the
# three-missed-heartbeat detection (FR-5.9).
RUNNER_SWEEP_QUEUE: Final = "agents-sweep"
RUNS_QUEUE: Final = api.RUNS_QUEUE
PROFILE_HEALTH_SCHEDULE: Final = "*/15 * * * *"  # every 15 minutes (plan default)
PROFILE_HEALTH_SCHEDULE_NAME: Final = "profile-health"

_profiles: Table = AgentProfile.__table__  # type: ignore[assignment]
_runners: Table = Runner.__table__  # type: ignore[assignment]
_runs: Table = RunRow.__table__  # type: ignore[assignment]
_events: Table = RunEventRow.__table__  # type: ignore[assignment]
_messages: Table = RunnerMessage.__table__  # type: ignore[assignment]


def _ctx(workspace_id: str) -> WorkspaceContext:
    return WorkspaceContext(UUID(workspace_id), SYSTEM_ACTOR)


def run_workflow_id(run_id: UUID) -> str:
    return f"run_skill:{run_id}"


# --- run_skill ------------------------------------------------------------------------------


def _packet_project(packet: TaskPacket) -> UUID | None:
    """The project a packet's body names (`project.id`), None when it names none."""
    project = packet.body.get("project")
    raw = project.get("id") if isinstance(project, dict) else None
    try:
        return UUID(str(raw)) if raw is not None else None
    except ValueError:
        return None


async def _with_token(ctx: WorkspaceContext, packet: TaskPacket) -> TaskPacket:
    """The packet with the run's task token in its callback (P2-02, R-27), issued from the
    profile's key: limited to the profile's project, or, for a plan or notify run on the
    master profile, to no project (Scott decision 30). A profile with no key yet dispatches
    as before (who creates a profile's key is open). The token is set here, inside the
    step, so it is never a workflow input or a step output."""
    async with tenant_session(ctx) as s:
        profile = (
            await s.execute(
                select(_profiles.c.role, _profiles.c.project_id, _profiles.c.api_key_id).where(
                    _profiles.c.id == packet.profile_id, _profiles.c.deleted_at.is_(None)
                )
            )
        ).first()
    if profile is None or profile.api_key_id is None:
        return packet  # an unknown profile is refused by the transport
    project = profile.project_id
    if project is None and packet.kind not in api.WORKSPACE_RUN_KINDS:
        project = _packet_project(packet)
    token = await api.issue_run_token(
        ctx,
        run_id=packet.run_id,
        kind=packet.kind,
        project_id=project,
        api_key_id=profile.api_key_id,
        now=SystemClock().now(),
    )
    callback = packet.callback or Callback(mcp_url=MCP_PATH, rest_base_url=REST_BASE)
    return packet.model_copy(update={"callback": callback.model_copy(update={"task_token": token})})


@DBOS.step()
async def dispatch_step(workspace_id: str, packet: dict[str, Any]) -> str | None:
    """Dispatch through the daemon transport with the run's task token; the refusal's
    reason when the agent is unavailable or no token can be issued (nothing was
    dispatched, and a token already issued is ended), None once the run is queued."""
    ctx = _ctx(workspace_id)
    task = TaskPacket.model_validate(packet)
    async with tenant_session(_ctx(workspace_id)) as s:
        if not await _archive.dispatch_allowed(s, task.profile_id):
            return "project_archived"  # P2-18: none while archived, archiving or unarchiving
    try:
        task = await _with_token(ctx, task)
    except (auth.ScopeEscalation, ValueError) as exc:
        return f"no task token: {exc}"
    try:
        await DaemonTransport(ctx, SystemClock()).dispatch(task)
    except api.AgentUnavailable as exc:
        await api.run_ended(ctx, task.run_id, now=SystemClock().now())
        return exc.reason
    except BaseException:
        # Any other failure ends the run too (R-27: the token lives no longer than its
        # run); this step does not retry, so finish_step never runs after it.
        try:
            await api.run_ended(ctx, task.run_id, now=SystemClock().now())
        except Exception:
            _log.exception("ending run %s after a failed dispatch failed too", task.run_id)
        raise
    faults.killpoint("agents.dispatch_step")  # the mailbox row has committed
    return None


def _checked_output(packet: TaskPacket, output: dict[str, Any] | None) -> str | None:
    """None when the output is valid (or its schema is not registered yet), else why not."""
    if output is None:
        return "no_json"
    ref = packet.output_schema
    spec = registry().versions(ref.family, ref.name).get(ref.version)
    if spec is None:
        return None
    try:
        spec.model.model_validate(output)
    except ValidationError as exc:
        return f"invalid_output: {exc.error_count()} errors"
    return None


def _outcome(packet: TaskPacket, message: dict[str, Any] | None) -> api.RunOutcome:
    if message is None:
        return api.RunOutcome(run_id=packet.run_id, status="timed_out", error="no result in time")
    status = message.get("status")
    if status == "runner_lost":
        return api.RunOutcome(run_id=packet.run_id, status="runner_lost", error="runner_lost")
    if status == "cancelled":  # a protocol-2 cancel, or the protocol-1 fallback (P2-07)
        return api.RunOutcome(
            run_id=packet.run_id, status="cancelled", error=message.get("error") or "cancelled"
        )
    output = message.get("output_json")
    if status == "succeeded":
        invalid = _checked_output(packet, output)
        if invalid is not None:
            return api.RunOutcome(
                run_id=packet.run_id, status="failed", output_json=output, error=invalid
            )
        return api.RunOutcome(run_id=packet.run_id, status="succeeded", output_json=output)
    return api.RunOutcome(
        run_id=packet.run_id,
        status="timed_out" if status == "timed_out" else "failed",
        output_json=output,
        error=message.get("error") or str(status),
    )


@DBOS.step()
async def finish_step(
    workspace_id: str, packet: dict[str, Any], message: dict[str, Any] | None
) -> dict[str, Any]:
    """The run's outcome, written to its `runs` row (and a `failed` run event when no
    result ended it); then the run's task token ends, whatever the outcome (P2-02)."""
    task = TaskPacket.model_validate(packet)
    ended = await _record_outcome(workspace_id, task, message)
    await api.run_ended(_ctx(workspace_id), task.run_id, now=SystemClock().now())
    return ended


async def _record_outcome(
    workspace_id: str, task: TaskPacket, message: dict[str, Any] | None
) -> dict[str, Any]:
    outcome = _outcome(task, message)
    now = SystemClock().now()
    async with tenant_session(_ctx(workspace_id)) as s:
        # A run already cancelled (the protocol-1 fallback, whose message to this workflow
        # is best effort) stays cancelled: a lost message must not turn it into `timed_out`.
        current = (
            await s.execute(
                select(_runs.c.status, _runs.c.error)
                .where(_runs.c.id == task.run_id)
                .with_for_update()
            )
        ).first()
        if current is not None and current.status == "cancelled":
            return api.RunOutcome(
                run_id=task.run_id, status="cancelled", error=current.error or "cancelled"
            ).model_dump(mode="json")
        await s.execute(
            update(_runs)
            .where(_runs.c.id == task.run_id)
            .values(
                status=outcome.status,
                finished_at=now,
                output=outcome.output_json,
                error=outcome.error,
            )
        )
        if message is None or message.get("status") == "runner_lost":
            await s.execute(
                insert(_events)
                .values(
                    run_id=task.run_id,
                    message_id=uuid5(task.run_id, "failed"),
                    kind="failed",
                    payload={"status": outcome.status, "error": outcome.error},
                )
                .on_conflict_do_nothing(index_elements=["workspace_id", "message_id"])
            )
    return outcome.model_dump(mode="json")


@DBOS.workflow(name="run_skill")
async def run_skill(workspace_id: str, packet: dict[str, Any]) -> dict[str, Any]:
    task = TaskPacket.model_validate(packet)
    refused = await dispatch_step(workspace_id, packet)
    if refused is not None:
        return api.RunOutcome(
            run_id=task.run_id, status="failed", error=f"agent_unavailable: {refused}"
        ).model_dump(mode="json")
    message = await DBOS.recv_async(
        topic=api.run_topic(task.run_id), timeout_seconds=task.timeout_s + RECV_GRACE_S
    )
    return await finish_step(workspace_id, packet, message)


async def start_run_skill(
    workspace_id: UUID, packet: TaskPacket
) -> "WorkflowHandleAsync[dict[str, Any]]":
    """Start `run_skill` in this process (a worker's own workflow); idempotent on the run."""
    with SetWorkflowID(run_workflow_id(packet.run_id)):
        return await DBOS.start_workflow_async(
            run_skill, str(workspace_id), packet.model_dump(mode="json")
        )


async def enqueue_run_skill(client: "DBOSClient", workspace_id: UUID, packet: TaskPacket) -> str:
    """Enqueue `run_skill` on the runs queue (partitioned by profile) from outside the
    worker; returns the workflow ID."""
    workflow_id = run_workflow_id(packet.run_id)
    await client.enqueue_async(
        {
            "queue_name": api.RUNS_QUEUE,
            "workflow_name": "run_skill",
            "workflow_id": workflow_id,
            "queue_partition_key": str(packet.profile_id),
        },
        str(workspace_id),
        packet.model_dump(mode="json"),
    )
    return workflow_id


# --- dispatch_run (P2-04, FR-5.4, SAF-5, R-23, R-29, R-30) -----------------------------------
#
# `DBOS.recv` appears only in workflow bodies, never in a step (R-30). Every step's row
# writes and events share one transaction guarded by a FOR UPDATE status read, so a step
# replayed after a crash writes and emits nothing twice.


class Prepared(BaseModel):
    """What `prepare_run` hands the workflow: an ended run, a refusal, or the run's caps."""

    status: str
    ended: bool = False  # the run had already ended (cancelled while queued)
    refusal: str | None = None
    max_active_seconds: float = 0.0
    ceiling_seconds: float = 0.0
    started_at_s: float = 0.0
    used_seconds: float = 0.0


class RunHandleData(BaseModel):
    """The dispatched run, as `stop_agent` needs it (never the token)."""

    run_id: UUID
    profile_id: UUID
    correlation_id: str
    refused: str | None = None  # the agent was unavailable: nothing was dispatched


dispatch_workflow_id = api.dispatch_workflow_id


def _requester(created_by: str) -> ActorRef | None:
    """Who moves the task to In progress for the run: the person or agent who asked for it
    (START is a human or agent edge); None for a system request, which leaves it."""
    actor = ActorRef(created_by)
    return None if tasks.actor_kind(actor) is tasks.ActorKind.SYSTEM else actor


@DBOS.step()
async def prepare_run(workspace_id: str, run_id: str) -> Prepared:
    """One transaction: `can_dispatch` again (the task may have changed while queued); the
    run queued -> running with `started_at` and its workflow id; the task to In progress as
    the requester; `run.started`. A replay after a crash finds the run running and answers
    the stored values; an ended run (cancelled while queued) is answered as it is."""
    run = UUID(run_id)
    now = SystemClock().now()
    async with tenant_session(_ctx(workspace_id)) as s:
        row = (
            (
                await s.execute(
                    select(*_runs.c, _profiles.c.project_id, _profiles.c.status.label("pstatus"))
                    .select_from(_runs.join(_profiles, _profiles.c.id == _runs.c.profile_id))
                    .where(_runs.c.id == run)
                    .with_for_update(of=_runs)
                )
            )
            .mappings()
            .one()
        )
        status = RunStatus(row["status"])
        if status in TERMINAL_STATUSES:
            return Prepared(status=status.value, ended=True)
        policy = await projects.get_policy(s, row["project_id"])
        active_cap, ceiling = api.run_caps(policy.max_run_minutes)
        if status in (RunStatus.RUNNING, RunStatus.WAITING_ON_HUMAN):
            return Prepared(
                status=status.value,
                max_active_seconds=active_cap,
                ceiling_seconds=ceiling,
                started_at_s=(row["started_at"] or now).timestamp(),
                used_seconds=row["active_seconds_used"],
            )
        try:
            task = await tasks.get_task(s, row["task_id"])
        except NotFound:  # trashed or purged while queued: the run fails, nothing retries
            return Prepared(status=status.value, refusal="task_not_found")
        others: list[str] = list(
            await s.scalars(
                select(_runs.c.kind).where(
                    _runs.c.task_id == row["task_id"],
                    _runs.c.id != run,
                    _runs.c.status.in_(api.ACTIVE_RUN),
                )
            )
        )
        refusal = can_dispatch(
            DispatchTask(
                label=task.label,
                status=task.status,
                active_kinds=frozenset(RunKind(k) for k in others),
            ),
            RunKind(row["kind"]),
            DispatchProfile(status=row["pstatus"]),
        )
        if refusal is not None:
            return Prepared(status=status.value, refusal=refusal.code)
        run_transition(status, RunStatus.RUNNING)
        await s.execute(
            update(_runs)
            .where(_runs.c.id == run)
            .values(
                status=RunStatus.RUNNING.value,
                started_at=now,
                workflow_id=DBOS.workflow_id,
                state_seq=row["state_seq"] + 1,
            )
        )
        actor = _requester(row["created_by"])
        if actor is not None and task.status in ("backlog", "today"):
            await tasks.change_status(
                s, actor, task.id, tasks.Status.IN_PROGRESS, task.version, now=now
            )
        await emit(
            s,
            RunStartedV1(
                run_id=run,
                task_id=row["task_id"],
                project_id=row["project_id"],
                kind=RunKind(row["kind"]),
            ),
            occurred_at=now,
        )
        mark_changed(s, api.LIVE_RUN, run)
    return Prepared(
        status=RunStatus.RUNNING.value,
        max_active_seconds=active_cap,
        ceiling_seconds=ceiling,
        started_at_s=now.timestamp(),
    )


def _redacted(packet: TaskPacket) -> dict[str, Any]:
    stored = packet.model_dump(mode="json")
    callback = stored.get("callback")
    if isinstance(callback, dict) and callback.get("task_token") is not None:
        stored["callback"] = {**callback, "task_token": api.REDACTED}
    return stored


@DBOS.step()
async def send_to_agent(workspace_id: str, run_id: str) -> RunHandleData:
    """Builds the run's packet (`build_packet`, P2-02), issues its task token from the
    profile's key and dispatches it through the profile's adapter, in one step, so the
    token is never a recorded step output. The runner dedupes `run` by run id (the mailbox
    row is uuid5(run, "run")), so a replay after a crash dispatches nothing twice. The
    packet is stored on the run with its token redacted."""
    ctx = _ctx(workspace_id)
    run = UUID(run_id)
    async with tenant_session(ctx) as s:
        row = (
            await s.execute(
                select(
                    _runs.c.task_id, _runs.c.kind, _runs.c.profile_id, _runs.c.correlation_id
                ).where(_runs.c.id == run)
            )
        ).one()
        runner_id = await s.scalar(
            select(_profiles.c.runner_id).where(_profiles.c.id == row.profile_id)
        )
    handle = RunHandleData(run_id=run, profile_id=row.profile_id, correlation_id=row.correlation_id)
    try:
        packet = await build_packet(
            RunKind(row.kind), task_id=row.task_id, run_id=run, profile_id=row.profile_id, ctx=ctx
        )
        packet = await _with_token(ctx, packet)
    except (auth.ScopeEscalation, ValueError) as exc:
        return handle.model_copy(update={"refused": f"no_packet: {exc}"[:200]})
    try:
        adapter = await api.adapter_for(row.profile_id, ctx=ctx)
        await adapter.dispatch(packet)
    except api.AgentUnavailable as exc:
        return handle.model_copy(update={"refused": f"agent_unavailable: {exc.reason}"[:200]})
    faults.killpoint("agents.send_to_agent.after_dispatch")  # dispatched, step not recorded
    async with tenant_session(ctx) as s:
        await s.execute(
            update(_runs)
            .where(_runs.c.id == run)
            .values(packet=_redacted(packet), runner_id=runner_id)
        )
    return handle


@DBOS.step()
async def now_s() -> float:
    """The time, through a step (determinism: a replay reads the recorded value)."""
    return SystemClock().now().timestamp()


@DBOS.step()
async def stop_agent(workspace_id: str, handle: dict[str, Any], reason: str) -> None:
    """Asks the agent to stop (a `cancel` to a protocol-2 runner; an older runner's run is
    marked cancelled by the transport, P2-07)."""
    data = RunHandleData.model_validate(handle)
    ctx = _ctx(workspace_id)
    adapter = await api.adapter_for(data.profile_id, ctx=ctx)
    _log.info("stopping run %s: %s", data.run_id, reason)
    await adapter.cancel(
        api.RunHandle(
            run_id=data.run_id,
            profile_id=data.profile_id,
            transport="daemon",
            correlation_id=data.correlation_id,
        )
    )


@DBOS.step()
async def finish_run(
    workspace_id: str, run_id: str, status: str, reason: str | None
) -> tuple[str, int]:
    """Ends the run once (`api.finish_run_in`): its row, tokens, closing log line,
    `run.finished` and, at a time limit, the `run_limit` review item. An already ended run
    answers its stored status and seq."""
    ctx = _ctx(workspace_id)
    async with tenant_session(ctx) as s:
        done = await api.finish_run_in(
            s, ctx, UUID(run_id), RunStatus(status), reason, now=SystemClock().now()
        )
    return done.status.value, done.seq


@DBOS.step()
async def park(workspace_id: str, run_id: str, used: float) -> int:
    """The run waits on a human: running -> waiting_on_human, with the active time used so
    far (a continuation keeps the budget); the new state seq."""
    return await _move(workspace_id, run_id, RunStatus.WAITING_ON_HUMAN, used)


@DBOS.step()
async def bump_state(workspace_id: str, run_id: str) -> int:
    """The human answered: waiting_on_human -> running; the new state seq."""
    return await _move(workspace_id, run_id, RunStatus.RUNNING, None)


async def _move(workspace_id: str, run_id: str, target: RunStatus, used: float | None) -> int:
    run = UUID(run_id)
    async with tenant_session(_ctx(workspace_id)) as s:
        row = (
            await s.execute(
                select(_runs.c.status, _runs.c.state_seq).where(_runs.c.id == run).with_for_update()
            )
        ).one()
        current = RunStatus(row.status)
        if current is target or target not in RUN_TRANSITIONS.get(current, frozenset()):
            return int(row.state_seq)  # a replay, or a run that already ended
        values: dict[str, Any] = {"status": target.value, "state_seq": row.state_seq + 1}
        if used is not None:
            values["active_seconds_used"] = used
        await s.execute(update(_runs).where(_runs.c.id == run).values(**values))
        mark_changed(s, api.LIVE_RUN, run)
    return int(row.state_seq) + 1


async def _end(workspace_id: str, run_id: str, status: RunStatus, reason: str | None) -> str:
    ended, seq = await finish_run(workspace_id, run_id, status.value, reason)
    await DBOS.set_event_async(f"state:{seq}", ended)
    return ended


def _signal(message: dict[str, Any]) -> tuple[str, str | None]:
    """A message on the run's topic as (kind, reason): `run.signal` deliveries, and the
    older shapes (`{"status": "runner_lost"}` from a run_skill-era sweep, `{"status":
    "cancelled"}` from the protocol-1 cancel fallback)."""
    kind = message.get("kind")
    if isinstance(kind, str):
        return kind, message.get("reason")
    status = message.get("status")
    if status == "runner_lost":
        return "runner_lost", api.RUNNER_LOST
    if status == "cancelled":
        return "cancel", message.get("error") or "cancelled"
    return "ignored", None


async def _supervise(  # noqa: PLR0917  # the plan's one loop over every signal
    workspace_id: str,
    run_id: str,
    handle: RunHandleData,
    budget: float,
    ceiling: float,
    started_at_s: float,
    used: float = 0.0,
) -> str:
    """Waits for the run's end: a result, a cancel or limit, a lost runner, a failed agent,
    the active-time cap (SAF-5; time waiting on a human does not count) or the wall-clock
    ceiling (R-29; waiting included). Both caps are enforced here, never by a DBOS
    workflow timeout, so the agent is stopped and the log and review item are left."""
    stored = handle.model_dump(mode="json")
    waiting, mark = False, await now_s()
    ceiling_at = started_at_s + ceiling
    faults.killpoint("agents.dispatch_run.waiting_recv")  # parked before the first recv
    while True:
        wall_left = max(ceiling_at - mark, 0.0)
        left = WAIT_SLICE_S if waiting else max(budget - used, 0.0)
        message = await DBOS.recv_async(
            api.run_topic(UUID(run_id)), timeout_seconds=min(left, wall_left)
        )
        now = await now_s()
        if not waiting:
            used += now - mark
        mark = now
        if message is None:
            if now >= ceiling_at:
                await stop_agent(workspace_id, stored, api.WALL_CLOCK_CEILING)
                return await _end(workspace_id, run_id, RunStatus.TIMED_OUT, api.WALL_CLOCK_CEILING)
            if not waiting and used >= budget:
                await stop_agent(workspace_id, stored, api.TIME_LIMIT)
                return await _end(workspace_id, run_id, RunStatus.TIMED_OUT, api.TIME_LIMIT)
            continue
        kind, reason = _signal(message)
        if kind == "result":
            return await _end(workspace_id, run_id, RunStatus.SUCCEEDED, None)
        if kind == "waiting":
            waiting = True
            seq = await park(workspace_id, run_id, used)
            await DBOS.set_event_async(f"state:{seq}", RunStatus.WAITING_ON_HUMAN.value)
        elif kind == "resumed":
            waiting = False
            seq = await bump_state(workspace_id, run_id)
            await DBOS.set_event_async(f"state:{seq}", RunStatus.RUNNING.value)
        elif kind in ("cancel", "limit"):
            await stop_agent(workspace_id, stored, reason or kind)
            return await _end(workspace_id, run_id, RunStatus.CANCELLED, reason or kind)
        elif kind == "runner_lost":
            return await _end(workspace_id, run_id, RunStatus.RUNNER_LOST, api.RUNNER_LOST)
        elif kind == "agent_failed":
            return await _end(workspace_id, run_id, RunStatus.FAILED, reason or "agent_failed")


@DBOS.workflow(name="dispatch_run")
async def dispatch_run(workspace_id: str, run_id: str) -> str:
    """A run from the queue to its end (P2-04): prepare (running, task In progress), send
    the packet, then supervise until a result, a stop or a limit; returns the terminal
    status. Its workflow id is the run id, so a redelivered `run.requested` runs it once."""
    prep = await prepare_run(workspace_id, run_id)
    if prep.ended:
        return prep.status
    if prep.refusal is not None:
        return await _end(workspace_id, run_id, RunStatus.FAILED, prep.refusal)
    handle = await send_to_agent(workspace_id, run_id)
    faults.killpoint("agents.dispatch_run.after_send")  # send_to_agent's output recorded
    if handle.refused is not None:
        return await _end(workspace_id, run_id, RunStatus.FAILED, handle.refused)
    return await _supervise(
        workspace_id,
        run_id,
        handle,
        prep.max_active_seconds,
        prep.ceiling_seconds,
        prep.started_at_s,
        prep.used_seconds,
    )


async def start_dispatch(
    workspace_id: UUID, run_id: UUID, project_id: UUID, priority: int | None
) -> None:
    """Enqueue `dispatch_run` on the runs queue, partitioned by project (two at a time per
    project, SAF-5). DBOS 3.1.0 refuses a deduplication id on a partitioned queue, so the
    workflow id (the run id) is the guard: DBOS returns the existing workflow for an id in
    use, finished or not; the partial unique index on active runs refuses a second run of
    the task and kind. Started in a fresh context: a subscriber runs inside a DBOS step,
    and DBOS refuses to start a workflow from one."""

    async def enqueue() -> None:
        options = SetEnqueueOptions(queue_partition_key=str(project_id), priority=priority)
        with SetWorkflowID(dispatch_workflow_id(run_id)), options:
            await DBOS.enqueue_workflow_async(
                RUNS_QUEUE, dispatch_run, str(workspace_id), str(run_id)
            )

    await asyncio.get_running_loop().create_task(enqueue(), context=contextvars.Context())


async def deliver_signal(
    workspace_id: UUID, run_id: UUID, kind: str, reason: str | None, key: str
) -> None:
    """Send a `run.signal` to the run's workflow, once per event (`key`). A workflow that
    does not exist yet is retried (the delivery raises), unless the run already ended."""
    message = {"kind": kind, "reason": reason}
    async with tenant_session(_ctx(str(workspace_id))) as s:
        row = (
            await s.execute(select(_runs.c.status, _runs.c.workflow_id).where(_runs.c.id == run_id))
        ).first()
    if row is None:
        return
    target = row.workflow_id or dispatch_workflow_id(run_id)
    try:
        await DBOS.send_async(target, message, topic=api.run_topic(run_id), idempotency_key=key)
    except DBOSNonExistentWorkflowError:
        if row.status in api.TERMINAL:
            return
        raise


# --- runner_sweep ---------------------------------------------------------------------------


@DBOS.step()
async def list_workspaces_step() -> list[str]:
    async with db.app_sessionmaker()() as s, s.begin():
        return [str(workspace_id) for workspace_id in await audit.workspace_ids(s)]


@DBOS.step()
async def sweep_workspace_step(workspace_id: str, now: datetime) -> list[tuple[str, str | None]]:
    """Mark the workspace's runners offline after three missed heartbeats at `now`, and
    end the runs still running on an offline runner `runner_lost`; returns those runs as
    (run id, workflow id)."""
    async with tenant_session(_ctx(workspace_id)) as s:
        runners = (
            await s.execute(
                select(_runners.c.id, _runners.c.status, _runners.c.last_heartbeat_at).where(
                    _runners.c.deleted_at.is_(None)
                )
            )
        ).all()
        offline = []
        for runner in runners:
            if runner_status(runner.last_heartbeat_at, now) != "offline":
                continue
            offline.append(runner.id)
            if runner.status != "offline":
                await s.execute(
                    update(_runners).where(_runners.c.id == runner.id).values(status="offline")
                )
                mark_changed(s, api.LIVE_RUNNER, runner.id)
        if not offline:
            return []
        profiles = select(_profiles.c.id).where(_profiles.c.runner_id.in_(offline))
        # A dispatch_run run (its workflow id is its run id) is ended by its workflow, told
        # through run.signal in this transaction (P2-04): the log, tokens and run.finished
        # follow its one end path.
        dispatched = (
            await s.execute(
                select(_runs.c.id).where(
                    _runs.c.status.in_((RunStatus.RUNNING.value, RunStatus.WAITING_ON_HUMAN.value)),
                    _runs.c.profile_id.in_(profiles),
                    _runs.c.workflow_id == cast(_runs.c.id, Text),
                )
            )
        ).all()
        for (run_id,) in dispatched:
            await emit(
                s,
                RunSignalV1(run_id=run_id, kind="runner_lost", reason=api.RUNNER_LOST),
                occurred_at=now,
            )
        lost = (
            await s.execute(
                update(_runs)
                .where(
                    _runs.c.status == "running",
                    _runs.c.profile_id.in_(profiles),
                    _runs.c.workflow_id.is_distinct_from(cast(_runs.c.id, Text)),
                )
                .values(status="runner_lost", finished_at=now, error="runner_lost")
                .returning(_runs.c.id, _runs.c.workflow_id)
            )
        ).all()
        for run in lost:
            await s.execute(
                insert(_events)
                .values(
                    run_id=run.id,
                    message_id=uuid5(run.id, "failed"),
                    kind="failed",
                    payload={"status": "runner_lost", "error": "runner_lost"},
                )
                .on_conflict_do_nothing(index_elements=["workspace_id", "message_id"])
            )
    return [(str(run.id), run.workflow_id) for run in lost]


@DBOS.workflow(name="runner_sweep")
async def runner_sweep(scheduled_at: datetime, context: Any) -> list[str]:
    """Scheduled each minute (DBOS passes the scheduled time and the schedule's context);
    returns the ids of the runs it ended `runner_lost`."""
    lost: list[str] = []
    for workspace_id in await list_workspaces_step():
        for run_id, workflow_id in await sweep_workspace_step(workspace_id, scheduled_at):
            if workflow_id is not None:
                await DBOS.send_async(
                    workflow_id, {"status": "runner_lost"}, topic=api.run_topic(UUID(run_id))
                )
            lost.append(run_id)
    return lost


# --- check_profile_health ---------------------------------------------------------------------


async def _coolify_base_url(ctx: WorkspaceContext) -> str | None:
    """The workspace's Coolify base URL (Settings > Coolify, P2-14), when it has one. The
    daemon only compares it with the profile's own `COOLIFY_BASE_URL` and never sends a
    token to it (Scott, decision 18)."""
    settings = await coolify.get_settings(ctx)
    return settings.base_url if settings is not None and settings.base_url else None


async def _reach_targets(s: Any, project_id: UUID | None) -> ReachTargets:
    repos = await projects.code_repos(s)
    apps = await projects.links_of_kind(s, "coolify_app")
    return reach_targets(
        project_id,
        [(link.project_id, link.value) for link in repos],
        [(link.project_id, link.value) for link in apps],
    )


@DBOS.step()
async def request_health_step(
    workspace_id: str, profile_id: str, request_id: str
) -> dict[str, Any] | None:
    """Ask for the profile's health: a `health_check` on its runner carrying the repos and
    apps its tokens must and must not reach (None: wait for the report), or the endpoint's
    answer (MCP profiles) or `offline` straight away."""
    ctx = _ctx(workspace_id)
    async with tenant_session(ctx) as s:
        row = (
            await s.execute(
                select(
                    _profiles.c.name,
                    _profiles.c.transport,
                    _profiles.c.endpoint,
                    _profiles.c.project_id,
                ).where(_profiles.c.id == UUID(profile_id))
            )
        ).one()
        targets = await _reach_targets(s, row.project_id)
    clock = SystemClock()
    if row.transport == "mcp_endpoint" and row.endpoint is not None:
        health = await McpEndpointTransport(row.endpoint, profile=row.name, clock=clock).health(
            UUID(profile_id)
        )
        return api.ProfileHealth(
            reachable=health.reachable,
            authenticated=health.authenticated,
            version=health.version,
            error=health.detail,
            status=health.status,
        ).model_dump(mode="json")
    scope = {
        "own_repos": list(targets.own_repos),
        "foreign_repos": list(targets.foreign_repos),
        "own_apps": list(targets.own_apps),
        "foreign_apps": list(targets.foreign_apps),
        "coolify_base_url": await _coolify_base_url(ctx)
        if targets.own_apps or targets.foreign_apps
        else None,
    }
    try:
        await DaemonTransport(ctx, clock).request_health(UUID(profile_id), UUID(request_id), scope)
    except api.AgentUnavailable as exc:
        return api.ProfileHealth(reachable=False, error=exc.reason, status="offline").model_dump(
            mode="json"
        )
    return None


def _health_from_report(report: dict[str, Any] | None) -> dict[str, Any]:
    if report is None:
        return api.ProfileHealth(
            reachable=False, error="no health report in time", status="offline"
        ).model_dump(mode="json")
    ok = bool(report.get("reachable")) and bool(report.get("profile_exists"))
    return api.ProfileHealth(
        reachable=bool(report.get("reachable")),
        authenticated=report.get("authenticated"),
        version=report.get("hermes_version"),
        profile_exists=report.get("profile_exists"),
        mcp_servers=list(report.get("mcp_servers") or []),
        error=report.get("error"),
        status="ok" if ok else "error",
        profile_version=report.get("profile_version"),
        mcp_server_details=report.get("mcp_server_details") or [],
        github=report.get("github"),
        coolify=report.get("coolify"),
    ).model_dump(mode="json")


def _foreign(
    verdict: ReachVerdict, targets: ReachTargets, names: dict[UUID, str]
) -> list[ForeignReach]:
    owners = dict(targets.owners)
    found = []
    for item in verdict.foreign:
        kind, _, target = item.partition(":")
        owner = owners.get(item)
        found.append(
            ForeignReach(
                kind="coolify" if kind == "coolify" else "github",
                target=target,
                project_id=owner,
                project_name=names.get(owner) if owner else None,
            )
        )
    return found


def _fix(extra: list[str], foreign: list[ForeignReach]) -> str:
    parts = []
    if extra:
        parts.append(
            f"Remove {', '.join(extra)} from the profile's MCP servers, or add them to the "
            "project's tool allowlist."
        )
    for kind in ("github", "coolify"):
        reached = [f for f in foreign if f.kind == kind]
        if not reached:
            continue
        where = ", ".join(
            f"{f.target} ({f.project_name})" if f.project_name else f.target for f in reached
        )
        scope = (
            "a fine-grained token with only this project's repositories"
            if kind == "github"
            else "a token of this project's own Coolify team"
        )
        parts.append(f"The {kind} token reaches {where}: replace it with {scope}.")
    return " ".join(parts)


def _dedupe_key(profile_id: str, extra: list[str], foreign: list[ForeignReach]) -> str:
    finding = json.dumps(
        {"extra": sorted(extra), "foreign": sorted(f"{f.kind}:{f.target}" for f in foreign)},
        sort_keys=True,
    )
    digest = hashlib.sha256(finding.encode()).hexdigest()[:32]
    return f"drift:{profile_id}:{digest}"


@DBOS.step()
async def record_health_step(
    workspace_id: str, profile_id: str, health: dict[str, Any], evaluate: bool = False
) -> None:
    """Write the profile's health. For a runner's report (`evaluate`), judge it first:
    drift from the project's allowlist and each token's reach (P2-10, SAF-2, SAF-3) set
    `degraded` or `warning`, and a server outside the allowlist or a token reaching another
    project queues one `drift` review item (its dedupe key hashes the finding, so the same
    report next time queues nothing)."""
    pid = UUID(profile_id)
    async with tenant_session(_ctx(workspace_id)) as s:
        row = (
            await s.execute(
                select(_profiles.c.name, _profiles.c.project_id).where(_profiles.c.id == pid)
            )
        ).one()
        checked = api.ProfileHealth.model_validate(health)
        values: dict[str, Any] = {"health_checked_at": SystemClock().now()}
        if evaluate and checked.status == "ok":
            checked = await _judged(s, row.project_id, checked)
            if checked.profile_version:
                values["profile_version"] = checked.profile_version
            if checked.extra or checked.foreign:
                payload = DriftPayload(
                    profile_id=pid,
                    profile=row.name,
                    extra=checked.extra,
                    foreign=checked.foreign,
                    fix=_fix(checked.extra, checked.foreign),
                )
                await tasks.add_review_item(
                    DRIFT.kind,
                    target=tasks.TargetRef(type="agent_profile", id=pid),
                    project_id=row.project_id,
                    payload=payload.model_dump(mode="json"),
                    dedupe_key=_dedupe_key(profile_id, checked.extra, checked.foreign),
                    session=s,
                )
        values["health"] = checked.model_dump(mode="json")
        await s.execute(update(_profiles).where(_profiles.c.id == pid).values(**values))
        mark_changed(s, api.LIVE_PROFILE, pid)


async def _judged(s: Any, project_id: UUID | None, checked: api.ProfileHealth) -> api.ProfileHealth:
    """The report with its drift, foreign reach and status filled in."""
    reported = [d.name for d in checked.mcp_server_details] or checked.mcp_servers
    drift = (
        allowlist_drift(reported, await projects.tool_allowlist(s, project_id))
        if project_id is not None
        else Drift(frozenset(), frozenset())
    )
    verdict = reach_verdict(checked.github, checked.coolify)
    targets = await _reach_targets(s, project_id)
    names = await projects.project_names(s, [p for _, p in targets.owners])
    return checked.model_copy(
        update={
            "status": profile_health(checked.reachable, checked.authenticated, drift, verdict),
            "extra": sorted(drift.extra),
            "missing": sorted(drift.missing),
            "foreign": _foreign(verdict, targets, names),
            "warnings": list(verdict.reasons),
        }
    )


@DBOS.workflow(name="check_profile_health")
async def check_profile_health(workspace_id: str, profile_id: str, request_id: str) -> None:
    health = await request_health_step(workspace_id, profile_id, request_id)
    evaluate = health is None
    if health is None:
        report = await DBOS.recv_async(topic=api.HEALTH_TOPIC, timeout_seconds=HEALTH_TIMEOUT_S)
        health = _health_from_report(report)
        evaluate = report is not None
    await record_health_step(workspace_id, profile_id, health, evaluate)


# --- provision_profile (P1-06) ----------------------------------------------------------------


class _Chosen(BaseModel):
    """What `choose_name_step` settled: the project's profile row, its name and mode, and
    the error that ends the provision before it starts (`invalid_name`, `no_project`, or
    `ready`: the profile needs nothing)."""

    profile_id: UUID | None
    name: str
    mode: api.ProvisionMode
    error_code: str | None = None


async def _taken_names(s: AsyncSession) -> set[str]:
    """Every profile name the workspace has used (deleted rows included: the unique index
    spans them) and every profile its runners reported."""
    names: set[str] = set((await s.scalars(select(_profiles.c.name))).all())
    inventories: list[list[dict[str, Any]]] = list(
        (await s.scalars(select(_runners.c.inventory))).all()
    )
    for inventory in inventories:
        names |= {str(p.get("name")) for p in inventory or []}
    return names


async def _link_refusal(s: AsyncSession, name: str | None) -> bool:
    """True when `name` cannot be linked: invalid, reserved, the master's, or another
    profile row's."""
    if name is None or name == MASTER_PROFILE_NAME:
        return True
    try:
        validate_profile_name(name)
    except InvalidProfileName:
        return True
    return await s.scalar(select(_profiles.c.id).where(_profiles.c.name == name)) is not None


def name_collision(exc: BaseException) -> bool:
    """True for the unique profile-name index refusing an insert: two projects whose names
    normalise alike picked the same free name at once, in different queue partitions. The
    loser's transaction rolled back, so its step runs again and sees the winner's name."""
    return isinstance(exc, IntegrityError) and "ux_agent_profiles_ws_name" in str(exc.orig)


@DBOS.step(retries_allowed=True, max_attempts=3, interval_seconds=0.1, should_retry=name_collision)
async def choose_name_step(
    workspace_id: str, project_id: str, mode: str, link_name: str | None
) -> dict[str, Any]:
    """The project's live profile row when it has one (a retry, or a replay), set
    `provisioning` (a `ready` one is left alone and ends the provision); otherwise a new
    row, `provisioning`: named after the project (create) or the linked name. A link to a
    name that cannot be linked gets a generated name and `invalid_name`, so the project
    still has its agent row to retry."""
    pid = UUID(project_id)
    async with tenant_session(_ctx(workspace_id)) as s:
        found = (
            await s.execute(
                select(
                    _profiles.c.id,
                    _profiles.c.name,
                    _profiles.c.status,
                    _profiles.c.provision_mode,
                ).where(
                    _profiles.c.role == "project",
                    _profiles.c.project_id == pid,
                    _profiles.c.deleted_at.is_(None),
                )
            )
        ).first()
        if found is not None and found.status == "ready":  # nothing left to provision
            return _Chosen(
                profile_id=found.id, name=found.name, mode="create", error_code="ready"
            ).model_dump(mode="json")
        if found is not None:
            await s.execute(
                update(_profiles).where(_profiles.c.id == found.id).values(status="provisioning")
            )
            mark_changed(s, api.LIVE_PROFILE, found.id)
            chosen_mode: api.ProvisionMode = "link" if found.provision_mode == "link" else "create"
            return _Chosen(profile_id=found.id, name=found.name, mode=chosen_mode).model_dump(
                mode="json"
            )
        names = await projects.project_names(s, [pid])
        if pid not in names:
            return _Chosen(
                profile_id=None, name=link_name or "", mode="create", error_code="no_project"
            ).model_dump(mode="json")
        chosen = _Chosen(profile_id=None, name=link_name or "", mode="link")
        if mode != "link" or await _link_refusal(s, link_name):
            chosen = _Chosen(
                profile_id=None,
                name=profile_name_for(names[pid], await _taken_names(s)),
                mode="create",
                error_code="invalid_name" if mode == "link" else None,
            )
        profile_id = await s.scalar(
            insert(_profiles)
            .values(
                name=chosen.name,
                role="project",
                project_id=pid,
                transport="daemon",
                status="not_provisioned" if chosen.error_code else "provisioning",
                provision_mode=chosen.mode,
            )
            .returning(_profiles.c.id)
        )
        mark_changed(s, api.LIVE_PROFILE, profile_id)
    return chosen.model_copy(update={"profile_id": profile_id}).model_dump(mode="json")


@DBOS.step()
async def pick_runner_step(workspace_id: str, link_name: str | None = None) -> str | None:
    """The runner for a project profile: for a link, a runner whose inventory lists the
    profile (online first, oldest first); otherwise, or when none lists it, the master's
    runner, else the online runners first, oldest first; None when the workspace has no
    runner."""
    async with tenant_session(_ctx(workspace_id)) as s:
        if link_name is not None:
            owner = await s.scalar(
                select(_runners.c.id)
                .where(
                    _runners.c.deleted_at.is_(None),
                    _runners.c.inventory.contains([{"name": link_name}]),
                )
                .order_by(
                    (_runners.c.status == "online").desc(), _runners.c.created_at, _runners.c.id
                )
                .limit(1)
            )
            if owner is not None:
                return str(owner)
        master_runner = await s.scalar(
            select(_profiles.c.runner_id).where(
                _profiles.c.role == "master",
                _profiles.c.deleted_at.is_(None),
                _profiles.c.runner_id.is_not(None),
            )
        )
        if master_runner is not None:
            return str(master_runner)
        runner = await s.scalar(
            select(_runners.c.id)
            .where(_runners.c.deleted_at.is_(None))
            .order_by((_runners.c.status == "online").desc(), _runners.c.created_at, _runners.c.id)
            .limit(1)
        )
    return None if runner is None else str(runner)


@DBOS.step()
async def send_provision_step(  # noqa: PLR0917  # the message's fields, spelled out
    workspace_id: str, workflow_id: str, profile_id: str, runner_id: str, name: str, mode: str
) -> None:
    """The profile on its runner and the `provision` mailbox row (message id
    uuid5(request id, "provision"), request id uuid5 of the workflow id, so a replayed step
    queues nothing new), then the NOTIFY that wakes the runner's socket."""
    request_id = api.provision_request_id(workflow_id)
    message = Provision(
        message_id=api.provision_message_id(request_id),
        correlation_id=workflow_id,
        sent_at=SystemClock().now(),
        request_id=request_id,
        profile=name,
        mode="link" if mode == "link" else "create",
        template=api.TEMPLATE_NAME,
        template_version=api.TEMPLATE_VERSION,
    )
    async with tenant_session(_ctx(workspace_id)) as s:
        await s.execute(
            update(_profiles)
            .where(_profiles.c.id == UUID(profile_id))
            .values(runner_id=UUID(runner_id))
        )
        await s.execute(
            insert(_messages)
            .values(
                runner_id=UUID(runner_id),
                message_id=message.message_id,
                direction="out",
                type=message.type,
                payload=message.model_dump(mode="json"),
            )
            .on_conflict_do_nothing(index_elements=["workspace_id", "message_id"])
        )
        await api.notify_runner(s, UUID(runner_id))
    faults.killpoint("agents.send_provision_step")  # the mailbox row has committed


def _failure(reply: ProvisionResult | None) -> tuple[str, str | None]:
    """The error code and detail of a provision that did not end ready."""
    if reply is None:
        return "timeout", "no answer from the runner in time"
    if reply.status == "failed":
        return reply.error_code or "hermes_error", reply.error
    return "hermes_error", f"unexpected answer {reply.status!r}"


@DBOS.step()
async def finish_provision_step(  # noqa: PLR0917  # the provision's facts, spelled out
    workspace_id: str,
    project_id: str,
    chosen: dict[str, Any],
    attempt: int,
    reply: dict[str, Any] | None,
    error_code: str | None,
) -> str:
    """`ready` (the profile's version recorded and its name added to its runner's
    inventory, so runs dispatch to it at once) or `not_provisioned` with a
    `provisioning_failed` review item on the project (one open item per project)."""
    pick = _Chosen.model_validate(chosen)
    answer = None if reply is None else ProvisionResult.model_validate(reply)
    outcome = (
        "not_provisioned" if error_code is not None else provision_outcome(answer, mode=pick.mode)
    )
    pid = UUID(project_id)
    assert pick.profile_id is not None  # noqa: S101  # choose_name_step wrote the row
    async with tenant_session(_ctx(workspace_id)) as s:
        if outcome == "ready" and answer is not None:
            runner_id = await s.scalar(
                update(_profiles)
                .where(_profiles.c.id == pick.profile_id)
                .values(status="ready", profile_version=answer.distribution_version)
                .returning(_profiles.c.runner_id)
            )
            if runner_id is not None:
                await _list_on_runner(s, runner_id, pick.name, answer.distribution_version)
                mark_changed(s, api.LIVE_RUNNER, runner_id)
        else:
            code, detail = (error_code, None) if error_code is not None else _failure(answer)
            await s.execute(
                update(_profiles)
                .where(_profiles.c.id == pick.profile_id)
                .values(status="not_provisioned")
            )
            await tasks.add_review_item(
                api.PROVISIONING_FAILED,
                target=tasks.TargetRef(type="project", id=pid),
                project_id=pid,
                payload=api.ProvisioningFailedPayload(
                    project_id=pid,
                    profile=pick.name,
                    mode=pick.mode,
                    error_code=code,
                    error=detail,
                    attempt=attempt,
                ).model_dump(mode="json"),
                dedupe_key=f"{api.PROVISIONING_FAILED}:{pid}",
                session=s,
            )
        mark_changed(s, api.LIVE_PROFILE, pick.profile_id)
    return outcome


async def _list_on_runner(s: AsyncSession, runner_id: UUID, name: str, version: str | None) -> None:
    """Add the profile to the runner's inventory until its next register reports it."""
    inventory = await s.scalar(
        select(_runners.c.inventory).where(_runners.c.id == runner_id).with_for_update()
    )
    listed = list(inventory or [])
    if any(p.get("name") == name for p in listed):
        return
    listed.append({"name": name, "distribution_name": None, "distribution_version": version})
    await s.execute(update(_runners).where(_runners.c.id == runner_id).values(inventory=listed))


@DBOS.workflow(name="provision_profile")
async def provision_profile(
    workspace_id: str, project_id: str, mode: str, link_name: str | None, attempt: int = 0
) -> str:
    """The project's Hermes profile, created from the template or linked, exactly once:
    the steps are idempotent, the runner's answer arrives on `provision:<project id>`
    (never inside a step, R-30), and no answer in `provision_timeout_s` counts as failed.
    Never retries an install by itself: a failure is a review item (accept retries)."""
    chosen = await choose_name_step(workspace_id, project_id, mode, link_name)
    if chosen["error_code"] in {"no_project", "ready"}:
        return str(chosen["error_code"])
    if chosen["error_code"] is not None:
        return await finish_provision_step(
            workspace_id, project_id, chosen, attempt, None, chosen["error_code"]
        )
    runner = await pick_runner_step(
        workspace_id, chosen["name"] if chosen["mode"] == "link" else None
    )
    if runner is None:
        return await finish_provision_step(
            workspace_id, project_id, chosen, attempt, None, "no_runner"
        )
    workflow_id = api.provision_workflow_id(UUID(project_id), attempt)
    await send_provision_step(
        workspace_id, workflow_id, chosen["profile_id"], runner, chosen["name"], chosen["mode"]
    )
    reply = await DBOS.recv_async(
        topic=api.provision_topic(UUID(project_id)), timeout_seconds=api.provision_timeout_s()
    )
    return await finish_provision_step(workspace_id, project_id, chosen, attempt, reply, None)


async def start_provision(  # the workflow's arguments, spelled out
    workspace_id: UUID,
    project_id: UUID,
    mode: api.ProvisionMode,
    link_name: str | None,
    *,
    attempt: int,
) -> None:
    """Enqueue `provision_profile` (workflow id `provision:<project id>[:<attempt>]`) on the
    runs queue, partitioned by project, once: DBOS returns the existing workflow for an id
    in use. (DBOS 3.1.0 refuses a deduplication id on a partitioned queue, so the workflow
    id is the only guard, and it is enough: it outlives the workflow, a deduplication id
    does not.)

    Subscribers run inside a DBOS step, and DBOS refuses to start a workflow from a step,
    so the enqueue runs in a fresh context (no enclosing workflow): the new workflow is a
    root of its own, and a re-run step enqueues the same ids again, which is a no-op."""

    async def enqueue() -> None:
        with (
            SetWorkflowID(api.provision_workflow_id(project_id, attempt)),
            SetEnqueueOptions(queue_partition_key=f"provision:{project_id}"),
        ):
            await DBOS.enqueue_workflow_async(
                RUNS_QUEUE,
                provision_profile,
                str(workspace_id),
                str(project_id),
                mode,
                link_name,
                attempt,
            )

    await asyncio.get_running_loop().create_task(enqueue(), context=contextvars.Context())


api.register_provision_starter(start_provision)


# --- enrich_task (P1-08, FR-4.4, FR-4.6, UX 5, UX 9) -------------------------------------------

ENRICH_RUNS: Final = UUID("6f1e0b8a-3c2d-5e4f-9a8b-7c6d5e4f3a21")  # uuid5 namespace of run ids
LABEL_POLL_S: Final = 0.5  # how often the enrichment looks for the label (plan default)
APPLY_ATTEMPTS: Final = 3  # a user write between read and apply: read, merge, try again
ENRICH_SKILL: Final = "enrich"
ENRICH_RESULT: Final = SchemaRef(family="enrichment", name="result", version=1)


def enrich_workflow_id(task_id: UUID, key: str) -> str:
    """`enrich:<task id>:<key>`: the key is the triggering event's id, so a redelivered
    event starts nothing new (the idempotency key; see `start_enrichment`)."""
    return f"enrich:{task_id}:{key}"


def _snapshot(task: tasks.TaskOut) -> TaskSnapshot:
    return TaskSnapshot(
        id=task.id,
        project_id=task.project_id,
        title=task.title,
        label=None if task.label is None else task.label.value,
        label_source=task.label_source,
        status=task.status.value,
        first_action=task.first_action,
        first_action_source=task.first_action_source,
        acceptance_criteria=task.acceptance_criteria,
        estimate_minutes=task.estimate_minutes,
        version=task.version,
        enrichment_status=task.enrichment_status,
    )


async def _read_snapshot(s: AsyncSession, task_id: UUID) -> TaskSnapshot | None:
    try:
        return _snapshot(await tasks.get_task(s, task_id))
    except NotFound:
        return None


async def _project_profile(s: AsyncSession, project_id: UUID) -> UUID | None:
    """The project's live agent profile (P1-06), None when it has none."""
    found: UUID | None = await s.scalar(
        select(_profiles.c.id).where(
            _profiles.c.role == "project",
            _profiles.c.project_id == project_id,
            _profiles.c.deleted_at.is_(None),
        )
    )
    return found


def _wanted(snap: TaskSnapshot, only: list[str] | None) -> list[str]:
    return [f for f in missing_fields(snap) if only is None or f in only]


async def agent_provisioned(project_id: UUID, *, ctx: WorkspaceContext) -> bool:
    """Whether the project has an agent enrichment can wait for (ready or offline); a
    project without a profile, or with one still `provisioning` or `not_provisioned`,
    has none (P1-06), whatever the clock says."""
    now = (api.enrichment_config().clock or SystemClock()).now()
    return await api.agent_for_project(project_id, now=now, ctx=ctx) != "not_provisioned"


@DBOS.step()
async def enrich_load_step(
    workspace_id: str, task_id: str, only: list[str] | None
) -> dict[str, Any] | None:
    """The task's snapshot, its project's name and the enrichment's settings (R-30, read
    here so a replay keeps them); None when the task is gone or needs nothing, or its
    project has no provisioned agent (no profile, or one still `provisioning` or
    `not_provisioned`): nothing is written to the task then, and its first action shows
    as pending. An offline agent goes on: the placeholder, then `agent_offline`."""
    ctx = _ctx(workspace_id)
    async with tenant_session(ctx) as s:
        snap = await _read_snapshot(s, UUID(task_id))
        if snap is None or not needs_enrichment(snap) or not _wanted(snap, only):
            return None
        names = await projects.project_names(s, [snap.project_id])
    config = api.enrichment_config()
    if not await agent_provisioned(snap.project_id, ctx=ctx):
        return None
    return {
        "snapshot": snap.model_dump(mode="json"),
        "project_name": names.get(snap.project_id, ""),
        "label_wait_s": config.label_wait_s,
        "run_timeout_s": config.run_timeout_s,
    }


@DBOS.step()
async def enrich_placeholder_step(
    workspace_id: str, snapshot: dict[str, Any], project_name: str
) -> None:
    """`pending`, with the Generation slot's placeholder first action (FR-4.6, its 2 s
    budget) when the task has none: shown until the agent's arrives. A slow or failing
    slot writes no placeholder, and the first action shows as pending."""
    snap = TaskSnapshot.model_validate(snapshot)
    placeholder = None
    if snap.first_action is None or not snap.first_action.strip():
        placeholder = await generation_api.placeholder_first_action(
            title=snap.title, project_name=project_name, project_id=snap.project_id
        )
    async with tenant_session(_ctx(workspace_id)) as s:
        await tasks.set_enrichment_status(s, snap.id, "pending", placeholder=placeholder)


@DBOS.step()
async def enrich_label_step(workspace_id: str, task_id: str) -> str | None:
    """The task's label now (None: still pending, or the task is gone)."""
    async with tenant_session(_ctx(workspace_id)) as s:
        snap = await _read_snapshot(s, UUID(task_id))
    return None if snap is None else snap.label


@DBOS.step()
async def enrich_availability_step(workspace_id: str, task_id: str, project_id: str) -> str:
    """The project agent's availability on the enrichment's clock; when it is not ready,
    the task's status says why (`agent_offline`, `not_provisioned`)."""
    ctx = _ctx(workspace_id)
    clock = api.enrichment_config().clock or SystemClock()
    state = await api.agent_for_project(UUID(project_id), now=clock.now(), ctx=ctx)
    if state != "ready":
        async with tenant_session(ctx) as s:
            status: tasks.EnrichmentStatus = (
                "agent_offline" if state == "offline" else "not_provisioned"
            )
            await tasks.set_enrichment_status(s, UUID(task_id), status)
    return state


@DBOS.step()
async def enrich_request_step(
    workspace_id: str, task_id: str, project_id: str, only: list[str] | None
) -> dict[str, Any] | None:
    """The enrichment request as the task is now, the project's profile, and `running`
    (set before the dispatch, so nothing writes the task while the run is out). None, and
    `done`, when nothing is missing any more (the user filled it meanwhile)."""
    async with tenant_session(_ctx(workspace_id)) as s:
        snap = await _read_snapshot(s, UUID(task_id))
        if snap is None:
            return None
        wanted = _wanted(snap, only) if snap.status != "done" else []
        if not wanted:
            await tasks.set_enrichment_status(s, snap.id, "done")
            return None
        request = await enrichment_request(s, snap.id, missing=wanted)
        profile_id = await _project_profile(s, UUID(project_id))
        if profile_id is None:  # removed while the enrichment waited
            await tasks.set_enrichment_status(s, snap.id, "not_provisioned")
            return None
        await tasks.set_enrichment_status(s, snap.id, "running")
    return {"request": request.model_dump(mode="json"), "profile_id": str(profile_id)}


def _checked_result(request: EnrichmentRequest, outcome: dict[str, Any]) -> EnrichmentResult | None:
    """The run's result when it succeeded and breaks no rule (schema, then
    `enrichment_errors`); None otherwise."""
    if outcome.get("status") != "succeeded":
        return None
    try:
        result = EnrichmentResult.model_validate(outcome.get("output_json"))
    except ValidationError:
        return None
    return None if enrichment_errors(request, result) else result


@DBOS.step()
async def enrich_fail_step(workspace_id: str, task_id: str) -> None:
    """`failed`: the task keeps what it has (a placeholder stays for the user to replace);
    the run's row holds the error."""
    async with tenant_session(_ctx(workspace_id)) as s:
        await tasks.set_enrichment_status(s, UUID(task_id), "failed")


def _history_median(request: EnrichmentRequest) -> int | None:
    actuals = [h.actual_minutes for h in request.estimate_history]
    return round(statistics.median(actuals)) if actuals else None


@DBOS.step()
async def enrich_plausibility_step(
    workspace_id: str, task_id: str, request: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any] | None:
    """Jev's `estimate_plausibility` Score for the estimate the enrichment would apply
    (FR-4.4, FR-11.4): the flag when an applied answer sits at an outer level, else None.
    Decisions down routes deterministic: no flag, no review item."""
    ctx = _ctx(workspace_id)
    req = EnrichmentRequest.model_validate(request)
    res = EnrichmentResult.model_validate(result)
    async with tenant_session(ctx) as s:
        snap = await _read_snapshot(s, UUID(task_id))
    if snap is None:
        return None
    patch = merge_enrichment(snap, res, requested=req.missing, estimate_range=ESTIMATE_RANGE)
    if patch.estimate_minutes is None:
        return None
    inputs = {
        "title": snap.title,
        "label": patch.label or snap.label,
        "estimate_minutes": patch.estimate_minutes,
        "first_action": patch.first_action or snap.first_action,
        "acceptance_criteria": list(res.acceptance_criteria),
        "history": [
            {"title": h.title, "estimate": h.estimate_minutes, "actual": h.actual_minutes}
            for h in req.estimate_history
        ],
    }
    with use_workspace(ctx):
        decision = await decisions.decide(
            decisions.DecisionPoint.ESTIMATE_PLAUSIBILITY,
            inputs,
            subject=decisions.SubjectRef(type="task", id=snap.id),
            project_id=snap.project_id,
        )
    answer = decision.answers.get("plausibility")
    score = answer if isinstance(answer, decisions.ScoreAnswer) else None
    flag = plausibility_flag(score, decision.route.value)
    if flag is None:
        return None
    return tasks.EstimateOutlierPayload(
        estimate_minutes=patch.estimate_minutes,
        flag=flag,
        score=None if score is None else score.score,
        history_median=_history_median(req),
        decision_id=decision.decision_id,
    ).model_dump(mode="json")


@DBOS.step()
async def enrich_apply_step(
    workspace_id: str,
    task_id: str,
    request: dict[str, Any],
    result: dict[str, Any],
    outlier: dict[str, Any] | None,
) -> str:
    """The result merged into the task as it is now (a user's edit made meanwhile wins,
    UX 9) and applied in one versioned write, `done`; an outlier flag for the estimate it
    applied queues one `estimate_outlier` item in the same transaction."""
    req = EnrichmentRequest.model_validate(request)
    res = EnrichmentResult.model_validate(result)
    flagged = None if outlier is None else tasks.EstimateOutlierPayload.model_validate(outlier)
    for attempt in range(APPLY_ATTEMPTS):
        try:
            async with tenant_session(_ctx(workspace_id)) as s:
                snap = await _read_snapshot(s, UUID(task_id))
                if snap is None:
                    return "gone"
                patch = merge_enrichment(
                    snap, res, requested=req.missing, estimate_range=ESTIMATE_RANGE
                )
                await tasks.apply_enrichment(
                    s, snap.id, tasks.EnrichmentWrite(**patch.model_dump()), snap.version
                )
                if flagged is not None and patch.estimate_minutes == flagged.estimate_minutes:
                    await tasks.add_estimate_outlier(s, snap.id, flagged)
            return "done"
        except StaleVersion:
            if attempt == APPLY_ATTEMPTS - 1:
                raise
    return "done"  # pragma: no cover  # the loop returns or raises


@DBOS.step()
async def enrich_follow_up_step(workspace_id: str, task_id: str, requested: list[str]) -> bool:
    """Whether the task, now that this enrichment is applied, gets an estimate-only
    follow-up (`rules.estimate_follow_up`: relabelled Human or Hybrid while it ran)."""
    async with tenant_session(_ctx(workspace_id)) as s:
        snap = await _read_snapshot(s, UUID(task_id))
    return snap is not None and estimate_follow_up(requested, snap)


@DBOS.workflow(name="enrich_task")
async def enrich_task(workspace_id: str, task_id: str, only: list[str] | None = None) -> str:
    """Enrichment by the project agent (P1-08): a placeholder first action first, a wait
    for the label (durable `DBOS.sleep_async`, up to `label_wait_s`), the project agent's
    availability (an offline agent degrades only its own project), the request, the
    `enrich` run (a child `run_skill`, run id derived from this workflow's id so a replay
    dispatches nothing twice), validation, the plausibility decision and the apply.
    Returns how it ended."""
    loaded = await enrich_load_step(workspace_id, task_id, only)
    if loaded is None:
        return "not_needed"
    snap = TaskSnapshot.model_validate(loaded["snapshot"])
    await enrich_placeholder_step(workspace_id, loaded["snapshot"], loaded["project_name"])
    # `pending` is on the task from here: whatever raises (the label wait, availability,
    # the request, the dispatch, the apply) ends it `failed`, a status that lets a later
    # relabel enrich again, and the workflow keeps its error. A worker killed meanwhile
    # (CancelledError, not an Exception) is left for DBOS recovery to resume.
    try:
        label: str | None = snap.label
        waited = 0.0
        while label is None and waited < loaded["label_wait_s"]:
            await DBOS.sleep_async(LABEL_POLL_S)
            waited += LABEL_POLL_S
            label = await enrich_label_step(workspace_id, task_id)
        state = await enrich_availability_step(workspace_id, task_id, str(snap.project_id))
        if state != "ready":
            return state
        built = await enrich_request_step(workspace_id, task_id, str(snap.project_id), only)
        if built is None:
            return "not_needed"
        request = EnrichmentRequest.model_validate(built["request"])
        run_id = uuid5(ENRICH_RUNS, DBOS.workflow_id or task_id)
        packet = TaskPacket(
            kind=RunKind.ENRICH,
            run_id=run_id,
            profile_id=UUID(built["profile_id"]),
            skill=ENRICH_SKILL,
            output_schema=ENRICH_RESULT,
            correlation_id=f"enrich:{task_id}",
            timeout_s=loaded["run_timeout_s"],
            prompt_text=render_prompt(ENRICH_SKILL, ENRICH_RESULT, built["request"]),
            body=built["request"],
        )
        with SetWorkflowID(run_workflow_id(run_id)):
            outcome = await run_skill(workspace_id, packet.model_dump(mode="json"))
        result = _checked_result(request, outcome)
        if result is None:
            await enrich_fail_step(workspace_id, task_id)
            return "failed"
        as_json = result.model_dump(mode="json")
        outlier = await enrich_plausibility_step(workspace_id, task_id, built["request"], as_json)
        ended = await enrich_apply_step(workspace_id, task_id, built["request"], as_json, outlier)
    except Exception:
        await enrich_fail_step(workspace_id, task_id)
        raise
    if ended == "done" and await enrich_follow_up_step(
        workspace_id, task_id, list(request.missing)
    ):
        # Enqueued from the workflow, so a replay finds its child rather than adding one.
        with (
            SetWorkflowID(enrich_workflow_id(UUID(task_id), f"estimate:{run_id}")),
            SetEnqueueOptions(queue_partition_key=str(snap.project_id)),
        ):
            await DBOS.enqueue_workflow_async(
                RUNS_QUEUE, enrich_task, workspace_id, task_id, [ESTIMATE]
            )
    return ended


async def start_enrichment(
    workspace_id: UUID,
    task_id: UUID,
    project_id: UUID,
    *,
    key: str,
    only: list[str] | None = None,
) -> None:
    """Enqueue `enrich_task` on the runs queue, partitioned by project (A9: an offline
    project agent never holds up another project), with workflow id
    `enrich:<task id>:<key>`. DBOS 3.1.0 refuses a deduplication id on a partitioned
    queue, so the workflow id is the idempotency key (the plan's `enrich:{task_id}:
    {version}` deduplication id; `task.updated` carries no version, so the key is the
    event's id). `only` narrows the fields asked for (a label change asks for the
    estimate). Like `start_provision`, the enqueue runs in a fresh context: subscribers
    run inside a DBOS step, which may not start a workflow."""

    async def enqueue() -> None:
        with (
            SetWorkflowID(enrich_workflow_id(task_id, key)),
            SetEnqueueOptions(queue_partition_key=str(project_id)),
        ):
            await DBOS.enqueue_workflow_async(
                RUNS_QUEUE, enrich_task, str(workspace_id), str(task_id), only
            )

    await asyncio.get_running_loop().create_task(enqueue(), context=contextvars.Context())


# --- profile_health_sweep ---------------------------------------------------------------------


@DBOS.step()
async def list_profiles_step(workspace_id: str) -> list[str]:
    """The ids of the workspace's live, unpaused profiles."""
    async with tenant_session(_ctx(workspace_id)) as s:
        ids: list[UUID] = list(
            await s.scalars(
                select(_profiles.c.id).where(
                    _profiles.c.deleted_at.is_(None), _profiles.c.status != "paused"
                )
            )
        )
    return [str(profile_id) for profile_id in ids]


@DBOS.workflow(name="profile_health_sweep")
async def profile_health_sweep(scheduled_at: datetime, context: Any) -> int:
    """Scheduled every 15 minutes (plan default): one `check_profile_health` per profile,
    with a request id per (profile, tick), so a replayed tick starts none twice."""
    started = 0
    found = [
        (workspace_id, profile_id)
        for workspace_id in await list_workspaces_step()
        for profile_id in await list_profiles_step(workspace_id)
    ]
    for workspace_id, profile_id in found:
        request_id = uuid5(UUID(profile_id), scheduled_at.isoformat())
        with (
            SetWorkflowID(api.health_workflow_id(request_id)),
            SetEnqueueOptions(queue_partition_key=f"profile:{profile_id}"),
        ):
            await DBOS.enqueue_workflow_async(
                RUNS_QUEUE, check_profile_health, workspace_id, profile_id, str(request_id)
            )
        started += 1
    return started


async def reconcile_runs(scheduled_at: datetime, context: Any) -> list[str]:
    """Fails the runs whose `dispatch_run` workflow ended while the row is active."""
    raise NotImplementedError


def schedules() -> list[Any]:
    """This module's DBOS schedules, applied by the worker after launch (the runner sweep
    has its own registration in `worker.py`)."""
    return [
        {
            "schedule_name": PROFILE_HEALTH_SCHEDULE_NAME,
            "workflow_fn": profile_health_sweep,
            "schedule": PROFILE_HEALTH_SCHEDULE,
            "queue_name": RUNNER_SWEEP_QUEUE,
        }
    ]
