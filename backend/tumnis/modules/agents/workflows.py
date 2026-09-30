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
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID, uuid5

from dbos import DBOS, SetEnqueueOptions, SetWorkflowID
from pydantic import BaseModel, ValidationError
from sqlalchemy import Table, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import audit, db, faults
from tumnis.core.clock import SystemClock
from tumnis.core.live import mark_changed
from tumnis.core.schemas import registry
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.agents import api
from tumnis.modules.agents.adapters.hermes import DaemonTransport, McpEndpointTransport
from tumnis.modules.agents.models import (
    AgentProfile,
    RunEventRow,
    Runner,
    RunnerMessage,
    RunRow,
)
from tumnis.modules.agents.packet_builder import TaskPacket
from tumnis.modules.agents.protocol import Provision, ProvisionResult
from tumnis.modules.agents.review_kinds import DRIFT, DriftPayload, ForeignReach
from tumnis.modules.agents.rules import (
    MASTER_PROFILE_NAME,
    Drift,
    InvalidProfileName,
    ReachTargets,
    ReachVerdict,
    allowlist_drift,
    profile_health,
    profile_name_for,
    provision_outcome,
    reach_targets,
    reach_verdict,
    runner_status,
    validate_profile_name,
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
_messages: Table = RunnerMessage.__table__  # type: ignore[assignment]


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


# --- enrich_task (P1-08): red-phase seam; the spec tests turn it green ---------------------


async def start_enrichment(
    workspace_id: UUID, task_id: UUID, project_id: UUID, *, key: str
) -> None:
    """Enqueue `enrich_task` for the task (workflow id `enrich:<task id>:<key>`)."""
    raise NotImplementedError("P1-08")


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
