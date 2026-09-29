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
  endpoint) and writes the profile's `health`.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID, uuid5

from dbos import DBOS, SetWorkflowID
from pydantic import ValidationError
from sqlalchemy import Table, select, update
from sqlalchemy.dialects.postgresql import insert

from tumnis.core import audit, db, faults
from tumnis.core.clock import SystemClock
from tumnis.core.live import mark_changed
from tumnis.core.schemas import registry
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.agents import api
from tumnis.modules.agents.adapters.hermes import DaemonTransport, McpEndpointTransport
from tumnis.modules.agents.models import AgentProfile, RunEventRow, Runner, RunRow
from tumnis.modules.agents.packet_builder import TaskPacket
from tumnis.modules.agents.rules import runner_status

if TYPE_CHECKING:
    from dbos import DBOSClient, WorkflowHandleAsync

RECV_GRACE_S: Final = 30  # the run's timeout plus this, then timed_out (plan default)
HEALTH_TIMEOUT_S: Final = 30
RUNS_PARTITION_CONCURRENCY: Final = 2  # runs at once per profile (plan default)
RUNNER_SWEEP_SCHEDULE: Final = "* * * * *"
RUNNER_SWEEP_NAME: Final = "runner-sweep"
RUNS_QUEUE: Final = api.RUNS_QUEUE

_profiles: Table = AgentProfile.__table__  # type: ignore[assignment]
_runners: Table = Runner.__table__  # type: ignore[assignment]
_runs: Table = RunRow.__table__  # type: ignore[assignment]
_events: Table = RunEventRow.__table__  # type: ignore[assignment]


def _ctx(workspace_id: str) -> WorkspaceContext:
    return WorkspaceContext(UUID(workspace_id), SYSTEM_ACTOR)


def run_workflow_id(run_id: UUID) -> str:
    return f"run_skill:{run_id}"


# --- run_skill ------------------------------------------------------------------------------


@DBOS.step()
async def dispatch_step(workspace_id: str, packet: dict[str, Any]) -> str | None:
    """Dispatch through the daemon transport; the refusal's reason when the agent is
    unavailable (nothing was written), None once the run is queued."""
    task = TaskPacket.model_validate(packet)
    try:
        await DaemonTransport(_ctx(workspace_id), SystemClock()).dispatch(task)
    except api.AgentUnavailable as exc:
        return exc.reason
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
    result ended it)."""
    task = TaskPacket.model_validate(packet)
    outcome = _outcome(task, message)
    now = SystemClock().now()
    async with tenant_session(_ctx(workspace_id)) as s:
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
        lost = (
            await s.execute(
                update(_runs)
                .where(_runs.c.status == "running", _runs.c.profile_id.in_(profiles))
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


@DBOS.step()
async def request_health_step(
    workspace_id: str, profile_id: str, request_id: str
) -> dict[str, Any] | None:
    """Ask for the profile's health: a `health_check` on its runner (None: wait for the
    report), or the endpoint's answer (MCP profiles) or `offline` straight away."""
    ctx = _ctx(workspace_id)
    async with tenant_session(ctx) as s:
        row = (
            await s.execute(
                select(_profiles.c.name, _profiles.c.transport, _profiles.c.endpoint).where(
                    _profiles.c.id == UUID(profile_id)
                )
            )
        ).one()
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
    try:
        await DaemonTransport(ctx, clock).request_health(UUID(profile_id), UUID(request_id))
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
    ).model_dump(mode="json")


@DBOS.step()
async def record_health_step(workspace_id: str, profile_id: str, health: dict[str, Any]) -> None:
    async with tenant_session(_ctx(workspace_id)) as s:
        await s.execute(
            update(_profiles)
            .where(_profiles.c.id == UUID(profile_id))
            .values(health=health, health_checked_at=SystemClock().now())
        )
        mark_changed(s, api.LIVE_PROFILE, UUID(profile_id))


@DBOS.workflow(name="check_profile_health")
async def check_profile_health(workspace_id: str, profile_id: str, request_id: str) -> None:
    health = await request_health_step(workspace_id, profile_id, request_id)
    if health is None:
        report = await DBOS.recv_async(topic=api.HEALTH_TOPIC, timeout_seconds=HEALTH_TIMEOUT_S)
        health = _health_from_report(report)
    await record_health_step(workspace_id, profile_id, health)
