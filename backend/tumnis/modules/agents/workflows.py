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
- `profile_health_sweep(scheduled_at, context)`: scheduled every 15 minutes; enqueues
  `check_profile_health` for every live, unpaused profile.
"""

import hashlib
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID, uuid5

from dbos import DBOS, SetEnqueueOptions, SetWorkflowID
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
from tumnis.modules.agents.review_kinds import DRIFT, DriftPayload, ForeignReach
from tumnis.modules.agents.rules import (
    Drift,
    ReachTargets,
    ReachVerdict,
    allowlist_drift,
    profile_health,
    reach_targets,
    reach_verdict,
    runner_status,
)
from tumnis.modules.coolify import api as coolify
from tumnis.modules.projects import api as projects
from tumnis.modules.tasks import api as tasks

if TYPE_CHECKING:
    from dbos import DBOSClient, WorkflowHandleAsync

RECV_GRACE_S: Final = 30  # the run's timeout plus this, then timed_out (plan default)
HEALTH_TIMEOUT_S: Final = 30
RUNS_PARTITION_CONCURRENCY: Final = 2  # runs at once per profile (plan default)
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
    result ended it)."""
    task = TaskPacket.model_validate(packet)
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
