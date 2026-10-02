"""agents public functions and DTOs; the only file other modules may import (P1-04).

The one agent interface (`AgentAdapter`, FR-14.6) and the run vocabulary (R-22), runners
(the daemons that dial in over `/ws/runner` with a device token) and agent profiles (the
Hermes profiles Tumnis may run: one master, one per project).
"""

import contextlib
import json
from collections.abc import Sequence
from datetime import date, datetime
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal, Protocol
from uuid import UUID, uuid4, uuid5

from prometheus_client import Gauge
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import (
    RowMapping,
    Table,
    Text,
    cast,
    func,
    insert,
    literal,
    select,
    text,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from tumnis.core import audit, fake_scripts
from tumnis.core.errors import ProblemError
from tumnis.core.ids import uuid7
from tumnis.core.limits import RUN_WALL_CLOCK_CEILING
from tumnis.core.live import mark_changed
from tumnis.core.logging import scrub_text
from tumnis.core.metrics import REGISTRY
from tumnis.core.outbox import emit
from tumnis.core.pagination import Cursor, Page, SortKey, paginate
from tumnis.core.routing import register_project_lookup
from tumnis.core.tenancy import WorkspaceContext, session_for, tenant_session
from tumnis.core.types import SYSTEM_ACTOR, ActorRef, Interval
from tumnis.core.versioning import NotFound, Version, update_versioned
from tumnis.modules.agents.adapters.port import (
    AgentAdapter,
    AgentCapabilities,
    AgentHealth,
    AgentUnavailable,
    RunEvent,
    RunHandle,
)
from tumnis.modules.agents.digest import (
    DigestEntryOut,
    DigestOut,
    append_entry,
    horizon_lag_seconds,
    read_digest,
    record_event,
)
from tumnis.modules.agents.human import (
    HUMAN_QUEUE,
    HUMAN_TOPIC,
    Allowed,
    ApprovalRequired,
    AskHumanIn,
    Denied,
    HumanApproval,
    HumanQuestion,
    HumanWaitOut,
    RequestApprovalIn,
    ask_human,
    check_action,
    configure_human_waits,
    long_poll_decision,
    request_approval,
)
from tumnis.modules.agents.models import (
    AgentPause,
    AgentProfile,
    RunEventRow,
    Runner,
    RunnerMessage,
    RunRow,
    StuckRequest,
)
from tumnis.modules.agents.packet_builder import (
    ENRICH_TIMEOUT_S_DEFAULT,
    STUCK_MAX_MINUTES,
    TaskPacket,
)
from tumnis.modules.agents.payloads import (
    AgentsPausedV1,
    AgentsResumedV1,
    RunFinishedV1,
    RunRequestedV1,
    RunSignalV1,
    SignalKind,
    StuckOutcome,
    StuckResolvedV1,
)
from tumnis.modules.agents.protocol import McpServerInfo, SchemaRef
from tumnis.modules.agents.relay import (
    NEEDS_APP,
    ReplyHandler,
    register_reply_handler,
    reply_handler,
)
from tumnis.modules.agents.review_kinds import (
    RESULT,
    RUN_LIMIT,
    ForeignReach,
    ResultPayload,
    RunLimitPayload,
)
from tumnis.modules.agents.rules import (
    ACTIVE_RUN_STATUSES,
    MAX_TASKS_PER_RUN_DEFAULT,
    NAME_RE,
    TERMINAL_STATUSES,
    DispatchProfile,
    DispatchTask,
    InvalidProfileName,
    PauseScope,
    PauseState,
    PauseView,
    Refusal,
    RunKind,
    RunnerStatus,
    RunStatus,
    TokenReach,
    allowlist_drift,
    can_dispatch,
    over_task_limit,
    pause_state,
    run_token_scopes,
    run_transition,
    runner_status,
    stuck_step_after,
    validate_profile_name,
)
from tumnis.modules.agents.skill_io import (
    EnrichmentRequest,
    EnrichmentResult,
    FocusMessage,
    NotifyBatch,
    NotifyEvent,
    NotifyItem,
    NotifyProject,
    NotifyRequest,
    NotifyReturnTo,
    NotifyTask,
    PlanningRequest,
    PlanningResult,
    ProjectAgentEntry,
)
from tumnis.modules.auth import api as auth
from tumnis.modules.knowledge import api as knowledge
from tumnis.modules.projects import api as projects
from tumnis.modules.tasks import api as tasks
from tumnis.seed import AgentSeed, RunnerSeed, SeedWriterUnavailableError, register_seed_writer

if TYPE_CHECKING:
    from dbos import DBOSClient

__all__ = [
    "HUMAN_QUEUE",
    "HUMAN_TOPIC",
    "NEEDS_APP",
    "AgentAdapter",
    "AgentAvailability",
    "AgentCapabilities",
    "AgentHealth",
    "AgentProfileOut",
    "AgentUnavailable",
    "Allowed",
    "ApprovalRequired",
    "AskHumanIn",
    "Denied",
    "DigestEntryOut",
    "DigestOut",
    "EnrichmentRequest",
    "EnrichmentResult",
    "FocusMessage",
    "ForeignReach",
    "HealthCheckAccepted",
    "HumanApproval",
    "HumanQuestion",
    "HumanWaitOut",
    "MasterAgentOut",
    "McpServerInfo",
    "NotifyBatch",
    "NotifyEvent",
    "NotifyItem",
    "NotifyProject",
    "NotifyRequest",
    "NotifyReturnTo",
    "NotifyTask",
    "PauseScope",
    "PlanningRequest",
    "PlanningResult",
    "ProfileIn",
    "ProfilePatch",
    "ProfileToolsOut",
    "ProjectAgentEntry",
    "ReplyHandler",
    "RequestApprovalIn",
    "RunEvent",
    "RunHandle",
    "RunKind",
    "RunOutcome",
    "RunStatus",
    "RunnerCreated",
    "RunnerIn",
    "RunnerOut",
    "SchemaRef",
    "SkillRunner",
    "TaskPacket",
    "TokenReach",
    "ToolServerOut",
    "append_entry",
    "ask_human",
    "check_action",
    "configure_human_waits",
    "enrich_ahead",
    "issue_run_token",
    "long_poll_decision",
    "master_agent",
    "notify_packet",
    "plan_packet",
    "planning_request",
    "read_digest",
    "record_event",
    "register_enrichment_starter",
    "register_reply_handler",
    "register_skill_runner",
    "reply_handler",
    "request_approval",
    "retry_provision",
    "run_ended",
    "run_log",
    "run_notify",
    "run_plan",
    "run_token_scopes",
    "set_profile_key",
    "task_activity_times",
]

DIGEST_HORIZON_LAG = Gauge(
    "tumnis_digest_horizon_lag_seconds",
    "Age of the oldest running app transaction, which holds the digest horizon back",
    registry=REGISTRY,
)


async def export_metrics(conn: AsyncConnection) -> None:
    """tumnis_digest_horizon_lag_seconds on every /metrics scrape (P2-03): a long
    transaction delays digests, never loses an entry. tumnis.wiring registers it."""
    async with AsyncSession(bind=conn) as s:
        DIGEST_HORIZON_LAG.set(await horizon_lag_seconds(s))


RUNNER_CHANNEL: Final = "runner_mailbox"  # NOTIFY {"runner": id, "close": bool}
RUN_EVENTS_CHANNEL: Final = "agents_run_events"  # NOTIFY {"run": id}
RUNS_QUEUE: Final = "runs"  # A9: run_skill and check_profile_health
HEALTH_TOPIC: Final = "health"  # check_profile_health receives the runner's report on it
LIVE_RUNNER: Final = "runner"
LIVE_PROFILE: Final = "agent_profile"
TERMINAL: Final = TERMINAL_STATUSES

AgentAvailability = Literal["ready", "offline", "not_provisioned"]
Name = Annotated[str, StringConstraints(pattern=NAME_RE)]


class RunnerIn(BaseModel):
    name: Name


class RunnerOut(BaseModel):
    id: UUID
    name: str
    host: str | None
    os: str | None
    daemon_version: str | None
    hermes_version: str | None
    protocol_version: int | None
    last_heartbeat_at: datetime | None
    status: RunnerStatus  # computed from the last heartbeat at read time
    profiles: list[str]  # names from its last register
    created_at: datetime


class RunnerCreated(RunnerOut):
    token: str  # the device token, shown once (an idempotent replay answers without it)


class ProfileIn(BaseModel):
    name: str = Field(max_length=63)
    role: Literal["master", "project"]
    transport: Literal["daemon", "mcp_endpoint"] = "daemon"
    project_id: UUID | None = None
    runner_id: UUID | None = None
    endpoint: str | None = Field(default=None, max_length=2048)


class ProfilePatch(BaseModel):
    runner_id: UUID | None = None
    endpoint: str | None = Field(default=None, max_length=2048)
    status: Literal["registered", "paused"] | None = None
    version: Version


HealthState = Literal["ok", "warning", "degraded", "offline", "unsupported", "error"]


class ProfileHealth(BaseModel):
    reachable: bool
    authenticated: bool | None = None
    version: str | None = None  # Hermes's
    profile_exists: bool | None = None
    mcp_servers: list[str] = []
    error: str | None = None
    # degraded: a server outside the allowlist or a token reaching another project (P2-10)
    status: HealthState = "ok"
    profile_version: str | None = None  # the profile's VERSION stamp (P2-10)
    mcp_server_details: list[McpServerInfo] = []
    github: TokenReach | None = None
    coolify: TokenReach | None = None
    extra: list[str] = []  # servers the project's allowlist does not name
    missing: list[str] = []  # allowed servers the profile lacks
    foreign: list[ForeignReach] = []
    warnings: list[str] = []


class ToolServerOut(BaseModel):
    name: str
    transport: Literal["stdio", "http"] | None  # None: only the name was reported
    target: str | None  # the command's name or the URL's host, never arguments
    allowed: bool | None  # None: no allowlist applies (the master profile)


class ProfileToolsOut(BaseModel):
    """Settings > Agents, one profile's tools, read-only (FR-5.12): the MCP servers it
    reported at its last health check, each matched against its project's allowlist
    now, and its tokens' reach."""

    profile_id: UUID
    profile_name: str
    project_id: UUID | None
    checked_at: datetime | None
    status: HealthState | None  # None before the first check
    reachable: bool | None
    authenticated: bool | None
    hermes_version: str | None
    profile_version: str | None
    allowlist: list[str]
    servers: list[ToolServerOut]
    extra: list[str]
    missing: list[str]
    github: TokenReach | None
    coolify: TokenReach | None
    foreign: list[ForeignReach] = []  # named foreign reach; the tokens' lists stand alone


class AgentProfileOut(BaseModel):
    id: UUID
    name: str
    role: Literal["master", "project"]
    transport: Literal["daemon", "mcp_endpoint"]
    project_id: UUID | None
    runner_id: UUID | None
    endpoint: str | None
    status: str
    profile_version: str | None
    health: ProfileHealth | None
    health_checked_at: datetime | None
    version: int
    created_at: datetime


class HealthCheckAccepted(BaseModel):
    request_id: UUID
    profile_id: UUID


class RunOutcome(BaseModel):
    """What `run_skill` returns: a terminal RunStatus value, the skill's JSON and the
    error when it failed."""

    run_id: UUID
    status: Literal["succeeded", "failed", "timed_out", "runner_lost", "cancelled"]
    output_json: dict[str, Any] | None = None
    error: str | None = None


# --- Rows ------------------------------------------------------------------------------------

_runners: Table = Runner.__table__  # type: ignore[assignment]
_profiles: Table = AgentProfile.__table__  # type: ignore[assignment]
_events: Table = RunEventRow.__table__  # type: ignore[assignment]
_messages: Table = RunnerMessage.__table__  # type: ignore[assignment]
STREAM_KINDS: Final = frozenset({"log", "tool_call", "file"})

_NOTIFY = text("SELECT pg_notify(:channel, :payload)")


async def notify_runner(s: AsyncSession, runner_id: UUID, *, close: bool = False) -> None:
    """Wake the api process holding the runner's socket when this transaction commits: it
    forwards the runner's queued mailbox rows (or closes the socket, `close`)."""
    payload = json.dumps({"runner": str(runner_id), "close": close})
    await s.execute(_NOTIFY, {"channel": RUNNER_CHANNEL, "payload": payload})


class _RunnerRow(BaseModel):
    id: UUID
    name: str
    host: str | None
    os: str | None
    daemon_version: str | None
    hermes_version: str | None
    protocol_version: int | None
    last_heartbeat_at: datetime | None
    inventory: list[dict[str, Any]]
    created_at: datetime


def _runner_out(row: _RunnerRow, now: datetime) -> RunnerOut:
    return RunnerOut(
        **row.model_dump(exclude={"inventory"}),
        status=runner_status(row.last_heartbeat_at, now),
        profiles=[str(p.get("name")) for p in row.inventory],
    )


async def _runner_row(s: AsyncSession, runner_id: UUID) -> _RunnerRow:
    found = (
        (
            await s.execute(
                select(_runners).where(_runners.c.id == runner_id, _runners.c.deleted_at.is_(None))
            )
        )
        .mappings()
        .first()
    )
    if found is None:
        raise NotFound("runners", runner_id)
    return _RunnerRow.model_validate(dict(found))


# --- Runners (session only, admin) ---------------------------------------------------------


async def list_runners(
    s: AsyncSession, *, now: datetime, cursor: str | None, limit: int
) -> Page[RunnerOut]:
    """Runners by name; `status` is judged from the last heartbeat at `now`."""
    page = await paginate(
        s,
        select(_runners).where(_runners.c.deleted_at.is_(None)),
        keys=[SortKey(_runners.c.name)],
        id_col=_runners.c.id,
        cursor=cursor,
        limit=limit,
        model=_RunnerRow,
    )
    return Page[RunnerOut](
        items=[_runner_out(row, now) for row in page.items], next_cursor=page.next_cursor
    )


async def create_runner(
    ctx: WorkspaceContext, s: AsyncSession, body: RunnerIn, *, now: datetime
) -> RunnerCreated:
    """A runner row and its device token, shown once (`runner.created`). 409
    `runner_exists` for a name in use."""
    taken = await s.scalar(
        select(_runners.c.id).where(_runners.c.name == body.name, _runners.c.deleted_at.is_(None))
    )
    if taken is not None:
        raise ProblemError(409, "runner_exists", "Another runner has this name")
    try:  # a racing insert, or a soft-deleted runner's name (the index spans deleted rows)
        async with s.begin_nested():
            created = (
                (await s.execute(insert(_runners).values(name=body.name).returning(*_runners.c)))
                .mappings()
                .one()
            )
    except IntegrityError as exc:
        if "ux_runners_ws_name" in str(exc.orig):
            raise ProblemError(409, "runner_exists", "Another runner has this name") from None
        raise
    row = _RunnerRow.model_validate(dict(created))
    await audit.record(
        s, "runner.created", target=("runner", row.id), details={"name": row.name}, occurred_at=now
    )
    token = await auth.issue_device_token(ctx, runner_id=row.id, now=now)
    mark_changed(s, LIVE_RUNNER, row.id)
    return RunnerCreated(**_runner_out(row, now).model_dump(), token=token)


async def rotate_runner_token(
    ctx: WorkspaceContext, s: AsyncSession, runner_id: UUID, *, now: datetime
) -> RunnerCreated:
    """A new device token for the runner, shown once; the old one stops at once and the
    runner's live socket is closed (`runner.token_rotated`)."""
    row = await _runner_row(s, runner_id)
    token = await auth.issue_device_token(ctx, runner_id=runner_id, now=now)
    await audit.record(s, "runner.token_rotated", target=("runner", runner_id), occurred_at=now)
    await notify_runner(s, runner_id, close=True)
    mark_changed(s, LIVE_RUNNER, runner_id)
    return RunnerCreated(**_runner_out(row, now).model_dump(), token=token)


# --- Profiles ----------------------------------------------------------------------------


class _ProfileRow(BaseModel):
    id: UUID
    name: str
    role: Literal["master", "project"]
    transport: Literal["daemon", "mcp_endpoint"]
    project_id: UUID | None
    runner_id: UUID | None
    endpoint: str | None
    status: str
    profile_version: str | None
    health: ProfileHealth | None
    health_checked_at: datetime | None
    version: int
    created_at: datetime


def _live_profiles() -> Any:
    return _profiles.c.deleted_at.is_(None)


async def _profile_row(s: AsyncSession, profile_id: UUID) -> _ProfileRow:
    found = (
        (await s.execute(select(_profiles).where(_profiles.c.id == profile_id, _live_profiles())))
        .mappings()
        .first()
    )
    if found is None:
        raise NotFound("agent_profiles", profile_id)
    return _ProfileRow.model_validate(dict(found))


def _profile_out(row: _ProfileRow) -> AgentProfileOut:
    return AgentProfileOut(**row.model_dump())


async def list_profiles(
    s: AsyncSession, *, cursor: str | None, limit: int, project_id: UUID | None = None
) -> Page[AgentProfileOut]:
    """Live profiles by name; with `project_id`, only that project's agent (P1-06)."""
    query = select(_profiles).where(_live_profiles())
    if project_id is not None:
        query = query.where(_profiles.c.project_id == project_id)
    page = await paginate(
        s,
        query,
        keys=[SortKey(_profiles.c.name)],
        id_col=_profiles.c.id,
        cursor=cursor,
        limit=limit,
        model=_ProfileRow,
    )
    return Page[AgentProfileOut](
        items=[_profile_out(row) for row in page.items], next_cursor=page.next_cursor
    )


async def _check_refs(
    s: AsyncSession, *, runner_id: UUID | None, project_id: UUID | None = None
) -> None:
    if runner_id is not None:
        await _runner_row(s, runner_id)
    if project_id is not None and not await projects.project_exists(s, project_id):
        raise NotFound("projects", project_id)


def _check_role(role: str, project_id: UUID | None) -> None:
    if role == "project" and project_id is None:
        raise ProblemError(422, "invalid_profile", "A project profile names its project")
    if role == "master" and project_id is not None:
        raise ProblemError(422, "invalid_profile", "A master profile names no project")


def _check_transport(transport: str, runner_id: UUID | None, endpoint: str | None) -> None:
    if transport == "daemon" and endpoint is not None:
        raise ProblemError(422, "invalid_profile", "A daemon profile has no endpoint")
    if transport == "mcp_endpoint" and (endpoint is None or runner_id is not None):
        raise ProblemError(
            422, "invalid_profile", "An mcp_endpoint profile has an endpoint and no runner"
        )


def _conflict(exc: IntegrityError) -> ProblemError:
    text_ = str(exc.orig)
    if "ux_agent_profiles_one_master" in text_:
        return ProblemError(409, "master_exists", "The workspace already has a master agent")
    if "ux_agent_profiles_one_project_agent" in text_:
        return ProblemError(409, "project_agent_exists", "The project already has an agent")
    if "ux_agent_profiles_ws_name" in text_:
        return ProblemError(409, "profile_exists", "Another profile has this name")
    raise exc


async def register_profile(s: AsyncSession, body: ProfileIn, *, now: datetime) -> AgentProfileOut:
    """Registers a Hermes profile Tumnis may run. 422 `invalid_profile_name` (NAME_RE or
    reserved) and `invalid_profile` (transport, or role and project_id, disagree); 404 for
    an unknown runner or project; 409 `master_exists`, `project_agent_exists`,
    `profile_exists`. The references are checked first, so a runner or project of another
    workspace is 404 whatever else the body holds."""
    await _check_refs(s, runner_id=body.runner_id, project_id=body.project_id)
    try:
        validate_profile_name(body.name)
    except InvalidProfileName as exc:
        raise ProblemError(422, exc.code, str(exc)) from None
    _check_transport(body.transport, body.runner_id, body.endpoint)
    _check_role(body.role, body.project_id)
    if body.role == "master":
        master = await s.scalar(
            select(_profiles.c.id).where(_profiles.c.role == "master", _live_profiles())
        )
        if master is not None:
            raise ProblemError(409, "master_exists", "The workspace already has a master agent")
    taken = await s.scalar(
        select(_profiles.c.id).where(_profiles.c.name == body.name, _live_profiles())
    )
    if taken is not None:
        raise ProblemError(409, "profile_exists", "Another profile has this name")
    try:
        async with s.begin_nested():
            created = (
                (
                    await s.execute(
                        insert(_profiles).values(**body.model_dump()).returning(*_profiles.c)
                    )
                )
                .mappings()
                .one()
            )
    except IntegrityError as exc:
        raise _conflict(exc) from None
    row = _ProfileRow.model_validate(dict(created))
    mark_changed(s, LIVE_PROFILE, row.id)
    return _profile_out(row)


async def update_profile(
    s: AsyncSession, profile_id: UUID, body: ProfilePatch, *, now: datetime
) -> AgentProfileOut:
    """Moves a profile to another runner or endpoint, or pauses it (versioned)."""
    row = await _profile_row(s, profile_id)
    values = body.model_dump(exclude_unset=True, exclude={"version"})
    runner_id = values.get("runner_id", row.runner_id)
    endpoint = values.get("endpoint", row.endpoint)
    await _check_refs(s, runner_id=values.get("runner_id"))
    _check_transport(row.transport, runner_id, endpoint)
    updated = await update_versioned(s, _profiles, profile_id, body.version, values)
    mark_changed(s, LIVE_PROFILE, profile_id)
    return _profile_out(_ProfileRow.model_validate(dict(updated)))


async def request_health_check(
    ctx: WorkspaceContext,
    s: AsyncSession,
    profile_id: UUID,
    *,
    now: datetime,
    client: "DBOSClient",
) -> HealthCheckAccepted:
    """Enqueues `check_profile_health` on the runs queue (workflow ID
    `profile-health:<request id>`); the worker asks the runner (or the endpoint) and writes
    the profile's `health`."""
    row = await _profile_row(s, profile_id)
    request_id = uuid4()
    await client.enqueue_async(
        {
            "queue_name": RUNS_QUEUE,
            "workflow_name": "check_profile_health",
            "workflow_id": health_workflow_id(request_id),
            "queue_partition_key": f"profile:{row.id}",
        },
        str(ctx.workspace_id),
        str(row.id),
        str(request_id),
    )
    return HealthCheckAccepted(request_id=request_id, profile_id=row.id)


def health_workflow_id(request_id: UUID) -> str:
    """The `check_profile_health` workflow of a request; the runner's report goes to it."""
    return f"profile-health:{request_id}"


def run_topic(run_id: UUID) -> str:
    """The DBOS topic a `run_skill` workflow receives its result (or runner_lost) on."""
    return f"run:{run_id}"


async def profile_tools(s: AsyncSession, profile_id: UUID) -> ProfileToolsOut:
    """The profile's MCP servers from its last health check, each matched against its
    project's allowlist as it stands now (the master names no project: no allowlist
    applies), with its tokens' reach."""
    row = await _profile_row(s, profile_id)
    health = row.health
    allowlist = list(await projects.tool_allowlist(s, row.project_id)) if row.project_id else None
    details = health.mcp_server_details if health else []
    reported = [d.name for d in details] or (health.mcp_servers if health else [])
    known = {d.name: d for d in details}
    servers = [
        ToolServerOut(
            name=name,
            transport=known[name].transport if name in known else None,
            target=known[name].target if name in known else None,
            allowed=None if allowlist is None else name in allowlist,
        )
        for name in dict.fromkeys(reported)
    ]
    drift = allowlist_drift(reported, allowlist) if allowlist is not None and health else None
    return ProfileToolsOut(
        profile_id=row.id,
        profile_name=row.name,
        project_id=row.project_id,
        checked_at=row.health_checked_at,
        status=health.status if health else None,
        reachable=health.reachable if health else None,
        authenticated=health.authenticated if health else None,
        hermes_version=health.version if health else None,
        profile_version=(health.profile_version if health else None) or row.profile_version,
        allowlist=allowlist or [],
        servers=servers,
        extra=sorted(drift.extra) if drift else [],
        missing=sorted(drift.missing) if drift else [],
        github=health.github if health else None,
        coolify=health.coolify if health else None,
        foreign=health.foreign if health else [],
    )


async def profile_health(ctx: WorkspaceContext, profile_id: UUID) -> ProfileHealth | None:
    """The profile's last health check, None before the first."""
    async with tenant_session(ctx) as s:
        return (await _profile_row(s, profile_id)).health


# --- The agent for a profile or a project --------------------------------------------------


async def adapter_for(profile_id: UUID, *, ctx: WorkspaceContext | None = None) -> AgentAdapter:
    """The AgentAdapter of the profile: HermesAgent over its transport, or the FakeAgent
    in fakes mode while no runner has ever connected."""
    from tumnis.core import tenancy  # noqa: PLC0415
    from tumnis.core.adapters.registry import current_mode, resolve  # noqa: PLC0415
    from tumnis.core.clock import SystemClock  # noqa: PLC0415
    from tumnis.modules.agents.adapters.hermes import (  # noqa: PLC0415
        DaemonTransport,
        HermesAgent,
        McpEndpointTransport,
    )

    ctx = ctx or tenancy.current()
    if ctx is None:
        raise RuntimeError("adapter_for needs a workspace context")
    async with tenant_session(ctx) as s:
        row = await _profile_row(s, profile_id)
        never_seen = True
        if row.runner_id is not None:
            runner = await _runner_row(s, row.runner_id)
            never_seen = runner.last_heartbeat_at is None
    mode = current_mode()
    if mode == "fake" and never_seen and row.transport == "daemon":
        fake: AgentAdapter = resolve("agents.hermes", "fake")
        return fake
    clock = SystemClock()
    if row.transport == "mcp_endpoint" and row.endpoint is not None:
        return HermesAgent(
            profile_id, McpEndpointTransport(row.endpoint, profile=row.name, clock=clock)
        )
    return HermesAgent(profile_id, DaemonTransport(ctx, clock))


async def agent_for_project(
    project_id: UUID, *, now: datetime, ctx: WorkspaceContext | None = None
) -> AgentAvailability:
    """ready | offline | not_provisioned for the project's agent profile."""
    from tumnis.core import tenancy  # noqa: PLC0415

    ctx = ctx or tenancy.current()
    if ctx is None:
        raise RuntimeError("agent_for_project needs a workspace context")
    async with tenant_session(ctx) as s:
        found = (
            (
                await s.execute(
                    select(_profiles).where(
                        _profiles.c.role == "project",
                        _profiles.c.project_id == project_id,
                        _live_profiles(),
                    )
                )
            )
            .mappings()
            .first()
        )
        if found is None:
            return "not_provisioned"
        row = _ProfileRow.model_validate(dict(found))
        if row.status in ("provisioning", "not_provisioned"):
            return "not_provisioned"
        if row.status == "paused":
            return "offline"
        if row.transport == "mcp_endpoint":
            return "ready"
        if row.runner_id is None:
            return "not_provisioned"
        runner = await _runner_row(s, row.runner_id)
    online = runner_status(runner.last_heartbeat_at, now) == "online"
    listed = row.name in {str(p.get("name")) for p in runner.inventory}
    return "ready" if online and listed else "offline"


# --- Project provisioning (P1-06) ------------------------------------------------------------

TEMPLATE_NAME: Final = "project-template"  # profiles/project-template, shipped by the daemon
TEMPLATE_VERSION: Final = "1.2.0"  # profiles/project-template/VERSION (a unit test holds them)
PROVISION_TIMEOUT_S_DEFAULT: Final = 300  # plan default; settings.agents.provision_timeout_s
PROVISIONING_FAILED: Final = "provisioning_failed"
ProvisionMode = Literal["create", "link"]
ProvisionErrorCode = Literal[
    "template_version_mismatch",
    "not_found",
    "hermes_error",
    "invalid_name",
    "timeout",
    "no_runner",
]
# The namespace of provisioning request ids (uuid5 of the workflow id): fixed forever, so a
# replayed step names the same request.
_PROVISION_NS: Final = UUID("6b0d7a52-2f4e-4c55-9a43-3a1f0e5c7d21")

_provisioning: dict[str, int] = {"timeout_s": PROVISION_TIMEOUT_S_DEFAULT}


class ProvisioningFailedPayload(BaseModel):
    """The `provisioning_failed` review item: the project's profile could not be created
    or linked. Accept retries provisioning; reject keeps the project without an agent.

    One payload for both producers (Scott decision 29): the provision workflow fills every
    field; the review queue's contract (P1-13) needs only `project_id`, `mode` and
    `error`, so `profile`, `error_code` and `attempt` are optional."""

    project_id: UUID
    mode: ProvisionMode
    error: str | None = Field(default=None, max_length=4096)
    profile: str | None = Field(default=None, max_length=63)
    error_code: ProvisionErrorCode | None = None
    attempt: int | None = Field(default=None, ge=0)


tasks.register_review_kind(
    tasks.ReviewKindSpec(
        kind=PROVISIONING_FAILED,
        owner_module="agents",
        payload_schema=ProvisioningFailedPayload,
        actions=("accept", "reject", "snooze"),  # accept = retry; reject = keep, no agent
        impact_scope="project",  # it blocks the whole project (FR-6.1)
    )
)


def configure_provisioning(*, timeout_s: int) -> None:
    """How long `provision_profile` waits for the runner's answer (the worker sets it from
    `settings.agents.provision_timeout_s`; tests shorten it, R-30)."""
    if timeout_s <= 0:
        raise ValueError("the provision timeout must be positive")
    _provisioning["timeout_s"] = timeout_s


def provision_timeout_s() -> int:
    return _provisioning["timeout_s"]


def provision_workflow_id(project_id: UUID, attempt: int = 0) -> str:
    """`provision:<project id>` for the first provision, `provision:<project id>:<n>` for
    retry n."""
    return f"provision:{project_id}" if attempt == 0 else f"provision:{project_id}:{attempt}"


def provision_topic(project_id: UUID) -> str:
    """The DBOS topic `provision_profile` receives the runner's answer on."""
    return f"provision:{project_id}"


def project_of_provision(workflow_id: str) -> UUID | None:
    """The project a provisioning workflow id names; None for any other id."""
    prefix, _, rest = workflow_id.partition(":")
    if prefix != "provision":
        return None
    try:
        return UUID(rest.partition(":")[0])
    except ValueError:
        return None


def provision_request_id(workflow_id: str) -> UUID:
    """The `request_id` of the workflow's `provision` message (deterministic)."""
    return uuid5(_PROVISION_NS, workflow_id)


def provision_message_id(request_id: UUID) -> UUID:
    """The mailbox message id of a `provision` request: a replayed step writes nothing new."""
    return uuid5(request_id, "provision")


class ProvisionStarter(Protocol):
    """Enqueues `provision_profile` once (`workflows.start_provision`)."""

    async def __call__(
        self,
        workspace_id: UUID,
        project_id: UUID,
        mode: ProvisionMode,
        link_name: str | None,
        *,
        attempt: int,
    ) -> None: ...


_starter: list[ProvisionStarter] = []


def register_provision_starter(starter: ProvisionStarter) -> None:
    """workflows registers its enqueue at import: api may not import workflows (the
    module's own import graph is acyclic), yet the subscriber and the retry start the
    workflow through api."""
    _starter[:] = [starter]


async def _start_provision(
    workspace_id: UUID,
    project_id: UUID,
    mode: ProvisionMode,
    link_name: str | None,
    attempt: int,
) -> None:
    if not _starter:
        raise RuntimeError("agents.workflows is not loaded: nothing can start a provision")
    await _starter[0](workspace_id, project_id, mode, link_name, attempt=attempt)


class EnrichmentStarter(Protocol):
    """Enqueues `enrich_task` once per key (`workflows.start_enrichment`)."""

    async def __call__(
        self,
        workspace_id: UUID,
        task_id: UUID,
        project_id: UUID,
        *,
        key: str,
        only: list[str] | None = None,
    ) -> None: ...


_enrichment_starter: list[EnrichmentStarter] = []


def register_enrichment_starter(starter: EnrichmentStarter) -> None:
    """workflows registers its enqueue at import, as for the provision starter: focus's
    next-task preparation (P4-01) starts an enrichment through api."""
    _enrichment_starter[:] = [starter]


async def enrich_ahead(
    ctx: WorkspaceContext, task_id: UUID, project_id: UUID, *, key: str, now: datetime
) -> bool:
    """The task's enrichment run ahead of time (P4-01, FR-10.6: Guardrail prepares the next
    task, so starting it costs nothing): workflow id `enrich:<task id>:<key>`, so a
    redelivered event starts nothing new. Nothing starts (False) when the project has no
    provisioned agent."""
    if await agent_for_project(project_id, now=now, ctx=ctx) == "not_provisioned":
        return False
    if not _enrichment_starter:
        raise RuntimeError("agents.workflows is not loaded: nothing can start an enrichment")
    await _enrichment_starter[0](ctx.workspace_id, task_id, project_id, key=key)
    return True


async def provision_project(
    workspace_id: UUID,
    project_id: UUID,
    *,
    mode: ProvisionMode,
    link_name: str | None,
) -> None:
    """Start the project's first provision (`project.created`): workflow id
    `provision:<project id>`, so a redelivered event starts nothing new (DBOS returns the
    existing workflow for an id in use)."""
    await _start_provision(workspace_id, project_id, mode, link_name, 0)


async def retry_provision(project_id: UUID, *, ctx: WorkspaceContext | None = None) -> None:
    """Provision the project's profile again (accepting its `provisioning_failed` item): a
    `not_provisioned` profile starts retry n+1 (`provision:<project id>:<n+1>`); one still
    `provisioning` restarts its current attempt, which starts nothing new while that
    workflow exists; a `ready` profile, or none, is left alone. The row changes before the
    workflow is enqueued, so a crash in between is healed by the next accept."""
    from tumnis.core import tenancy  # noqa: PLC0415

    ctx = ctx or tenancy.current()
    if ctx is None:
        raise RuntimeError("retry_provision needs a workspace context")
    async with tenant_session(ctx) as s:
        row = (
            await s.execute(
                select(
                    _profiles.c.id,
                    _profiles.c.name,
                    _profiles.c.status,
                    _profiles.c.provision_mode,
                    _profiles.c.provision_attempts,
                )
                .where(
                    _profiles.c.role == "project",
                    _profiles.c.project_id == project_id,
                    _live_profiles(),
                )
                .with_for_update()
            )
        ).first()
        if row is None or row.status not in {"not_provisioned", "provisioning"}:
            return
        attempt: int = row.provision_attempts
        if row.status == "not_provisioned":
            attempt += 1
            await s.execute(
                update(_profiles)
                .where(_profiles.c.id == row.id)
                .values(status="provisioning", provision_attempts=attempt)
            )
            mark_changed(s, LIVE_PROFILE, row.id)
    mode: ProvisionMode = "link" if row.provision_mode == "link" else "create"
    await _start_provision(
        ctx.workspace_id,
        project_id,
        mode,
        row.name if mode == "link" else None,
        attempt,
    )


async def master_registry(*, ctx: WorkspaceContext | None = None) -> list[ProjectAgentEntry]:
    """The master's registry (the `agents` of its planning packet): every project agent of
    the workspace with its project's name, status and runner, by project name."""
    from tumnis.core import tenancy  # noqa: PLC0415

    ctx = ctx or tenancy.current()
    if ctx is None:
        raise RuntimeError("master_registry needs a workspace context")
    async with tenant_session(ctx) as s:
        rows = (
            await s.execute(
                select(
                    _profiles.c.project_id,
                    _profiles.c.name,
                    _profiles.c.status,
                    _runners.c.name.label("runner"),
                )
                .select_from(
                    _profiles.outerjoin(
                        _runners,
                        (_runners.c.id == _profiles.c.runner_id) & _runners.c.deleted_at.is_(None),
                    )
                )
                .where(
                    _profiles.c.role == "project",
                    _profiles.c.project_id.is_not(None),
                    _live_profiles(),
                )
            )
        ).all()
        names = await projects.project_names(s, [row.project_id for row in rows])
    entries = [
        ProjectAgentEntry(
            project_id=row.project_id,
            project_name=names[row.project_id],
            profile=row.name,
            status=row.status,
            runner=row.runner,
        )
        for row in rows
        if row.project_id in names
    ]
    return sorted(entries, key=lambda e: (e.project_name.casefold(), str(e.project_id)))


# --- The run log (P2-07) -------------------------------------------------------------------


async def run_log(s: AsyncSession, run_id: UUID) -> list[RunEvent]:
    """A run's events as the run view shows them: in the order they arrived, with the
    stream lines (`log`, `tool_call`, `file`) in `seq` order among themselves, so a line
    the daemon replayed late still takes its place. P2-04 serves it at
    `GET /v1/runs/{id}/events`."""
    rows = (
        (
            await s.execute(
                select(_events)
                .where(_events.c.run_id == run_id, _events.c.deleted_at.is_(None))
                .order_by(_events.c.created_at, _events.c.id)
            )
        )
        .mappings()
        .all()
    )
    events = [
        RunEvent(
            run_id=row["run_id"],
            message_id=row["message_id"],
            kind=row["kind"],
            payload=row["payload"],
            at=row["created_at"],
        )
        for row in rows
    ]
    slots = [i for i, event in enumerate(events) if event.kind in STREAM_KINDS]
    lines = sorted((events[i] for i in slots), key=lambda e: int(e.payload.get("seq") or 0))
    for slot, line in zip(slots, lines, strict=True):
        events[slot] = line
    return events


# --- Task tokens (P2-02, R-27, Scott decisions 30 and 31) ------------------------------------

REDACTED: Final = "[redacted]"  # what a stored packet's token reads once its run ended
WORKSPACE_RUN_KINDS: Final = frozenset({RunKind.PLAN, RunKind.NOTIFY})  # master runs


async def issue_run_token(
    ctx: WorkspaceContext,
    *,
    run_id: UUID,
    kind: RunKind,
    project_id: UUID | None,
    api_key_id: UUID,
    now: datetime,
) -> str:
    """The run's task token (shown once): the kind's scopes that the profile's key holds
    (`run_token_scopes`), limited to the run's project, valid until `run_ended`. A plan or
    notify run on the master profile has no project and gets a workspace-scoped token that
    reaches no project (decision 30); every other run names its project (ValueError).
    `auth.ScopeEscalation` when the key is missing or revoked."""
    if project_id is None and kind not in WORKSPACE_RUN_KINDS:
        raise ValueError(f"a {kind.value} run's token names its project")
    held = await auth.key_scopes(ctx, api_key_id)
    if held is None:
        raise auth.ScopeEscalation("the profile's key is missing or revoked")
    return await auth.issue_task_token(
        ctx,
        run_id=run_id,
        project_id=project_id,
        api_key_id=api_key_id,
        scopes=run_token_scopes(kind, held),
        now=now,
    )


async def run_ended(ctx: WorkspaceContext, run_id: UUID, *, now: datetime) -> int:
    """Every finished run calls this, whatever its status (idempotent): its task tokens
    are revoked (refused in every process within a second, `token_expired`), and in the
    same transaction the token in the stored `run` mailbox message is overwritten with
    REDACTED, so no backup or dump of the database holds a live or recent token (decision
    31). Returns how many tokens it revoked."""
    async with tenant_session(ctx) as s:
        revoked = await auth.revoke_task_tokens_for_run(ctx, run_id, now=now, session=s)
        await _redact_run_message(s, run_id)
    await fake_scripts.redact_run_token(run_id, REDACTED)  # fakes only (R-37)
    return revoked


async def _redact_run_message(s: AsyncSession, run_id: UUID) -> None:
    message_id = uuid5(run_id, "run")  # the dispatch's mailbox row (DaemonTransport)
    row = (
        await s.execute(
            select(_messages.c.id, _messages.c.payload)
            .where(_messages.c.message_id == message_id, _messages.c.deleted_at.is_(None))
            .with_for_update()
        )
    ).first()
    if row is None:
        return
    payload = dict(row.payload or {})
    packet = payload.get("packet")
    if not isinstance(packet, dict):
        return
    callback = packet.get("callback")
    if not isinstance(callback, dict) or callback.get("task_token") in (None, REDACTED):
        return
    payload["packet"] = {**packet, "callback": {**callback, "task_token": REDACTED}}
    await s.execute(update(_messages).where(_messages.c.id == row.id).values(payload=payload))


async def set_profile_key(
    ctx: WorkspaceContext, profile_id: UUID, api_key_id: UUID, *, now: datetime
) -> None:
    """Links the API key the profile's runs issue their task tokens from. 404 for an
    unknown profile, 422 `invalid_api_key` for a key that is missing or revoked."""
    if await auth.key_scopes(ctx, api_key_id) is None:
        raise ProblemError(422, "invalid_api_key", "The key is missing or revoked")
    async with tenant_session(ctx) as s:
        await _profile_row(s, profile_id)
        await s.execute(
            update(_profiles).where(_profiles.c.id == profile_id).values(api_key_id=api_key_id)
        )
        mark_changed(s, LIVE_PROFILE, profile_id)


async def profile_key(s: AsyncSession, profile_id: UUID) -> UUID | None:
    """The key a profile's runs issue task tokens from; None when none is linked."""
    return await s.scalar(
        select(_profiles.c.api_key_id).where(_profiles.c.id == profile_id, _live_profiles())
    )


# --- Runs (P2-04, FR-5.4, FR-5.5, FR-5.8, SAF-5, R-23, R-29) ---------------------------------

LIVE_RUN: Final = "run"
LIVE_PAUSE: Final = "agent_pause"  # the kill switch's state in the app (P2-09)
RUN_EVENTS_LIMIT_DEFAULT: Final = 100
RUN_EVENTS_LIMIT_MAX: Final = 500
# A protocol-2 `stream` line's kind -> the run event's kind (the runner's handler and the
# fake runner store them alike).
STREAM_EVENT_KIND: Final = {"log": "log", "tool_call": "tool_call", "file_touched": "file"}
LOG_LINE_MAX_BYTES: Final = 8 * 1024  # a longer line is cut with a marker (plan default)
CUT_MARKER: Final = " [cut]"
ACTIVE_RUN: Final = frozenset(s.value for s in ACTIVE_RUN_STATUSES)
TIME_LIMIT: Final = "time_limit"
WALL_CLOCK_CEILING: Final = "wall_clock_ceiling"
RUNNER_LOST: Final = "runner_lost"
STOPPED_BY_USER: Final = "stopped_by_user"
KILLSWITCH: Final = "killswitch"  # the workspace pause (P2-09)
PROJECT_PAUSED: Final = "project_paused"  # a project pause (P2-09)
TASKS_PER_RUN: Final = "tasks_per_run"  # the run created too many tasks (SAF-5, P2-09)
# Limits that stop a run as `cancelled` with a `run_limit` review item (SAF-5: hitting a
# limit stops the run and puts it in the review queue).
CANCEL_LIMITS: Final = frozenset({TASKS_PER_RUN})
# The system line that closes a run's log, by stop reason (or status when none names one).
CLOSING_LINES: Final[dict[str, str]] = {
    TIME_LIMIT: "Stopped at the time limit",
    WALL_CLOCK_CEILING: "Stopped at the wall-clock limit",
    RUNNER_LOST: "Stopped: the runner stopped answering",
    STOPPED_BY_USER: "Stopped by you",
    KILLSWITCH: "Stopped: all agents were paused",
    PROJECT_PAUSED: "Stopped: the project's agents were paused",
    TASKS_PER_RUN: "Stopped: the run created more tasks than its limit",
    "cancelled": "Stopped",
    "failed": "Stopped: the run failed",
    "timed_out": "Stopped at the time limit",
    "workflow_cancelled": "Stopped: the run's workflow ended",
    "workflow_error": "Stopped: the run's workflow ended",
}
# `reconcile_runs` (P2-04): the DBOS statuses of an ended `dispatch_run` workflow, and the
# stop reason a run still active then ends with.
ENDED_WORKFLOW_REASONS: Final[dict[str, str]] = {
    "CANCELLED": "workflow_cancelled",
    "ERROR": "workflow_error",
    "MAX_RECOVERY_ATTEMPTS_EXCEEDED": "workflow_error",
}
_runs: Table = RunRow.__table__  # type: ignore[assignment]
_limits: dict[str, float | None] = {
    "active_cap_s": None,  # None: the project policy's max_run_minutes
    "ceiling_s": RUN_WALL_CLOCK_CEILING.total_seconds(),
}


def dispatch_workflow_id(run_id: UUID) -> str:
    """A `dispatch_run` workflow's id is its run's id: enqueueing it twice runs it once."""
    return str(run_id)


def configure_runs(
    active_cap_seconds: float | None = None, wall_clock_ceiling_seconds: float | None = None
) -> None:
    """The run time caps (R-30): tests shorten them on the real clock; no arguments puts
    the plan defaults back (the project's max_run_minutes, and 24 hours)."""
    for value in (active_cap_seconds, wall_clock_ceiling_seconds):
        if value is not None and value <= 0:
            raise ValueError("a run time cap must be positive")
    _limits["active_cap_s"] = active_cap_seconds
    _limits["ceiling_s"] = (
        wall_clock_ceiling_seconds
        if wall_clock_ceiling_seconds is not None
        else RUN_WALL_CLOCK_CEILING.total_seconds()
    )


def run_caps(max_run_minutes: int) -> tuple[float, float]:
    """(active-time cap, wall-clock ceiling) in seconds for a run of a project whose
    policy allows `max_run_minutes`."""
    active = _limits["active_cap_s"]
    ceiling = _limits["ceiling_s"]
    assert ceiling is not None  # noqa: S101  # configure_runs always sets it
    return (active if active is not None else max_run_minutes * 60.0), ceiling


class RunRequested(BaseModel):
    """`POST /v1/tasks/{task_id}/run`'s answer (202)."""

    run_id: UUID
    status: Literal["queued"] = "queued"


class RunRequestIn(BaseModel):
    kind: Literal["task", "stuck"] = "task"


class RunOut(BaseModel):
    """A run as the run view shows it."""

    id: UUID
    task_id: UUID | None
    project_id: UUID | None
    kind: RunKind
    status: RunStatus
    stop_reason: str | None
    rerun_of: UUID | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    error: str | None = None
    active_seconds_used: float = 0.0
    # The profile's VERSION the daemon reported in the run's `status{started}` (R-25,
    # P2-12); None until one arrives.
    profile_version: str | None = None


class RunEventOut(BaseModel):
    seq: int
    message_id: UUID
    kind: str
    payload: dict[str, Any]
    at: datetime


class RunEventsPage(BaseModel):
    """One page of a run's events by `seq` (a cursor, not `Page`): the next page asks
    `after_seq=next_after_seq`; an empty page means the client has everything so far."""

    items: list[RunEventOut]
    next_after_seq: int | None


def _refusal(refusal: Refusal) -> ProblemError:
    status = 422 if refusal.code == "label_not_runnable" else 409
    return ProblemError(status, refusal.code, refusal.detail)


def _already_active() -> ProblemError:
    return ProblemError(409, "run_already_active", "The task already has an active run")


async def _project_profile(s: AsyncSession, project_id: UUID) -> RowMapping | None:
    return (
        (
            await s.execute(
                select(_profiles.c.id, _profiles.c.status).where(
                    _profiles.c.role == "project",
                    _profiles.c.project_id == project_id,
                    _live_profiles(),
                )
            )
        )
        .mappings()
        .first()
    )


async def request_run(  # the plan's signature, plus the context and session
    task_id: UUID,
    kind: RunKind,
    *,
    unattended: bool = False,
    priority: int | None = None,
    rerun_of: UUID | None = None,
    ctx: WorkspaceContext | None = None,
    session: AsyncSession | None = None,
    now: datetime | None = None,
) -> UUID:
    """Makes the run (status queued, tainted from the task, `created_by` the requester)
    and, in the same transaction, `run.requested`, whose subscriber enqueues `dispatch_run`
    on the runs queue (R-23: the Run button, reject-and-rerun and every later caller come
    here). 409 `run_already_active` (also when a concurrent request won the partial unique
    index), `status_not_runnable` or `no_ready_profile`, 422 `label_not_runnable`, 404 for
    a task the caller cannot see; 409 `agents_paused` while the workspace or the task's
    project is paused (P2-09)."""
    from tumnis.core import tenancy  # noqa: PLC0415
    from tumnis.core.clock import SystemClock  # noqa: PLC0415

    ctx = ctx or tenancy.current()
    if ctx is None:
        raise RuntimeError("request_run needs a workspace context")
    at = now or SystemClock().now()
    async with session_for(ctx, session) as s:
        task = await tasks.get_task(s, task_id)
        if await pause_state_for(s, task.project_id) != "running":
            raise ProblemError(
                409, "agents_paused", "Agents are paused; a person resumes them in the app"
            )
        profile = await _project_profile(s, task.project_id)
        kinds: list[str] = list(
            await s.scalars(
                select(_runs.c.kind).where(
                    _runs.c.task_id == task_id, _runs.c.status.in_(ACTIVE_RUN)
                )
            )
        )
        active = frozenset(RunKind(k) for k in kinds)
        refusal = can_dispatch(
            DispatchTask(label=task.label, status=task.status, active_kinds=active),
            kind,
            None if profile is None else DispatchProfile(status=profile["status"]),
        )
        if refusal is not None:
            raise _refusal(refusal)
        assert profile is not None  # noqa: S101  # can_dispatch refused a missing one
        run_id = uuid7()
        try:
            async with s.begin_nested():
                await s.execute(
                    insert(_runs).values(
                        id=run_id,
                        task_id=task_id,
                        profile_id=profile["id"],
                        kind=kind.value,
                        status=RunStatus.QUEUED.value,
                        correlation_id=f"run:{run_id}",
                        tainted=task.tainted,
                        rerun_of=rerun_of,
                    )
                )
        except IntegrityError:
            raise _already_active() from None
        await emit(
            s,
            RunRequestedV1(
                run_id=run_id,
                task_id=task_id,
                project_id=task.project_id,
                kind=kind,
                priority=priority,
                unattended=unattended,
                rerun_of=rerun_of,
            ),
            occurred_at=at,
        )
        mark_changed(s, LIVE_RUN, run_id)
    return run_id


def _run_select() -> Any:
    return select(*_runs.c, _profiles.c.project_id).select_from(
        _runs.join(_profiles, _profiles.c.id == _runs.c.profile_id)
    )


async def get_run(s: AsyncSession, run_id: UUID) -> RunOut:
    """The run (404 when the caller cannot see it)."""
    row = (
        (await s.execute(_run_select().where(_runs.c.id == run_id, _runs.c.deleted_at.is_(None))))
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("runs", run_id)
    return RunOut.model_validate(dict(row))


async def _run_project_id(s: AsyncSession, run_id: UUID) -> UUID | None:
    found: UUID | None = await s.scalar(
        select(_profiles.c.project_id)
        .select_from(_runs.join(_profiles, _profiles.c.id == _runs.c.profile_id))
        .where(_runs.c.id == run_id, _runs.c.deleted_at.is_(None))
    )
    return found


async def run_project(ctx: WorkspaceContext, run_id: UUID) -> UUID | None:
    """The project of a run's profile; None for a run the caller cannot see (or a master
    run, which names no project). Registered as the `lookup:runs` project lookup."""
    async with tenant_session(ctx) as s:
        return await _run_project_id(s, run_id)


register_project_lookup("runs", run_project)


async def run_events_page(
    s: AsyncSession, run_id: UUID, *, after_seq: int | None = None, limit: int = 100
) -> RunEventsPage:
    """The run's events after `after_seq`, in the order they were stored, at most `limit`
    (FR-5.5). 404 for a run the caller cannot see."""
    await get_run(s, run_id)
    stmt = (
        select(_events)
        .where(
            _events.c.run_id == run_id,
            _events.c.deleted_at.is_(None),
            _events.c.seq.is_not(None),
        )
        .order_by(_events.c.seq)
        .limit(limit)
    )
    if after_seq is not None:
        stmt = stmt.where(_events.c.seq > after_seq)
    rows = (await s.execute(stmt)).mappings().all()
    items = [
        RunEventOut(
            seq=row["seq"],
            message_id=row["message_id"],
            kind=row["kind"],
            payload=row["payload"],
            at=row["created_at"],
        )
        for row in rows
    ]
    return RunEventsPage(items=items, next_after_seq=items[-1].seq if items else after_seq)


def log_text(line: str) -> str:
    """A log line as it is stored: redacted (P0-16) before storage, never after, and cut at
    LOG_LINE_MAX_BYTES with a marker."""
    clean = scrub_text(line)
    raw = clean.encode("utf-8")
    if len(raw) <= LOG_LINE_MAX_BYTES:
        return clean
    keep = LOG_LINE_MAX_BYTES - len(CUT_MARKER.encode())
    return raw[:keep].decode("utf-8", errors="ignore") + CUT_MARKER


async def record_run_event(
    s: AsyncSession, run_id: UUID, message_id: UUID, kind: str, payload: dict[str, Any]
) -> None:
    """One event of a run (a stream line, a status, an artifact), stored once per message
    id in the caller's transaction, with a notice for the run view. The run's row is locked
    before the event takes its `seq`, as every run-event writer does: a run's events then
    commit in seq order, so a reader paging with `after_seq` never skips one although the
    sequence is global. The runner's handler and the fake runner both write through it."""
    await s.execute(select(_runs.c.id).where(_runs.c.id == run_id).with_for_update())
    await s.execute(
        pg_insert(_events)
        .values(run_id=run_id, message_id=message_id, kind=kind, payload=payload)
        .on_conflict_do_nothing(index_elements=["workspace_id", "message_id"])
    )
    await s.execute(
        _NOTIFY, {"channel": RUN_EVENTS_CHANNEL, "payload": json.dumps({"run": str(run_id)})}
    )


async def add_system_line(s: AsyncSession, run_id: UUID, line: str, *, name: str) -> None:
    """A system `log` line in the run's log, once per `name` (message id uuid5(run, name))."""
    await s.execute(
        pg_insert(_events)
        .values(
            run_id=run_id,
            message_id=uuid5(run_id, name),
            kind="log",
            payload={"kind": "log", "text": log_text(line), "source": "system"},
        )
        .on_conflict_do_nothing(index_elements=["workspace_id", "message_id"])
    )
    await s.execute(
        _NOTIFY, {"channel": RUN_EVENTS_CHANNEL, "payload": json.dumps({"run": str(run_id)})}
    )


class Finished(BaseModel):
    status: RunStatus
    seq: int
    changed: bool  # False: the run had already ended (nothing was written)


async def finish_run_in(
    s: AsyncSession,
    ctx: WorkspaceContext,
    run_id: UUID,
    status: RunStatus,
    reason: str | None,
    *,
    now: datetime,
) -> Finished:
    """Ends the run in the caller's transaction, once: status, finished_at and stop_reason;
    its task tokens revoked and redacted from what is stored (R-27, decision 31); a closing
    system line in its log for anything but success; `run.finished`; a `run_limit` review
    item for a time limit or a cancel at a limit (`CANCEL_LIMITS`, P2-09). The task keeps
    its status. A run already ended (the sweep, the protocol-1 cancel fallback, a replayed
    step) is left as it is: its stored status and seq come back and nothing is emitted
    twice."""
    row = (
        await s.execute(
            select(
                _runs.c.status,
                _runs.c.state_seq,
                _runs.c.task_id,
                _runs.c.kind,
                _runs.c.started_at,
            )
            .where(_runs.c.id == run_id)
            .with_for_update()
        )
    ).first()
    if row is None:
        raise NotFound("runs", run_id)
    current = RunStatus(row.status)
    if current in TERMINAL_STATUSES:
        return Finished(status=current, seq=row.state_seq, changed=False)
    if current is RunStatus.WAITING_ON_HUMAN and status is RunStatus.SUCCEEDED:
        # A result posted while the run waited on a human: the wait ends first (waiting ->
        # running), then the run succeeds; the state table has no direct edge.
        run_transition(current, RunStatus.RUNNING)
        current = RunStatus.RUNNING
    run_transition(current, status)
    seq = row.state_seq + 1
    await s.execute(
        update(_runs)
        .where(_runs.c.id == run_id)
        .values(status=status.value, finished_at=now, stop_reason=reason, state_seq=seq)
    )
    await auth.revoke_task_tokens_for_run(ctx, run_id, now=now, session=s)
    await _redact_run_message(s, run_id)
    await _redact_stored_packet(s, run_id)
    # The fake runner's last packet (compose.test only; a no-op otherwise, R-37).
    await fake_scripts.redact_run_token(run_id, REDACTED)
    if status is not RunStatus.SUCCEEDED:
        line = CLOSING_LINES.get(reason or "", CLOSING_LINES.get(status.value, "Stopped"))
        await add_system_line(s, run_id, line, name="closed")
    await emit(
        s,
        RunFinishedV1(
            run_id=run_id,
            task_id=row.task_id,
            kind=RunKind(row.kind),
            status=status.value,
            stop_reason=reason,
            duration_s=(
                None if row.started_at is None else max((now - row.started_at).total_seconds(), 0.0)
            ),
        ),
        occurred_at=now,
    )
    if status is RunStatus.TIMED_OUT or (status is RunStatus.CANCELLED and reason in CANCEL_LIMITS):
        await tasks.add_review_item(
            RUN_LIMIT,
            target=tasks.TargetRef(type="run", id=run_id),
            project_id=await _run_project_id(s, run_id),
            payload=RunLimitPayload(
                run_id=run_id, task_id=row.task_id, reason=reason or TIME_LIMIT
            ).model_dump(mode="json"),
            dedupe_key=f"run_limit:{run_id}",
            session=s,
        )
    mark_changed(s, LIVE_RUN, run_id)
    return Finished(status=status, seq=seq, changed=True)


async def _redact_stored_packet(s: AsyncSession, run_id: UUID) -> None:
    packet: dict[str, Any] | None = await s.scalar(
        select(_runs.c.packet).where(_runs.c.id == run_id)
    )
    if packet is None:
        return
    callback = packet.get("callback")
    if not isinstance(callback, dict) or callback.get("task_token") in (None, REDACTED):
        return
    redacted = {**packet, "callback": {**callback, "task_token": REDACTED}}
    await s.execute(update(_runs).where(_runs.c.id == run_id).values(packet=redacted))


async def signal_run(  # the signal, plus the context, session and time
    ctx: WorkspaceContext,
    run_id: UUID,
    kind: SignalKind,
    *,
    reason: str | None = None,
    session: AsyncSession | None = None,
    now: datetime | None = None,
) -> None:
    """Tells the run's workflow something (`run.signal`, which `agents.deliver_run_signal`
    sends with the event id as the idempotency key): neither the api nor the runner
    handler calls DBOS itself. 404 for a run the caller cannot see."""
    from tumnis.core.clock import SystemClock  # noqa: PLC0415

    async with session_for(ctx, session) as s:
        await get_run(s, run_id)
        await emit(
            s,
            RunSignalV1(run_id=run_id, kind=kind, reason=reason),
            occurred_at=now or SystemClock().now(),
        )


async def cancel_run(
    ctx: WorkspaceContext,
    run_id: UUID,
    *,
    now: datetime,
    reason: str = STOPPED_BY_USER,
    session: AsyncSession | None = None,
) -> RunOut:
    """Stop (FR-5.5): a queued or held run ends `cancelled` here (its workflow then finds
    it ended and stops); a running or waiting one gets `run.signal{cancel}`, and its
    workflow stops the agent through the adapter (the api never calls out); an ended run
    is left as it is. 404 for a run the caller cannot see."""
    async with session_for(ctx, session) as s:
        status = await s.scalar(
            select(_runs.c.status)
            .where(_runs.c.id == run_id, _runs.c.deleted_at.is_(None))
            .with_for_update()
        )
        if status is None:
            raise NotFound("runs", run_id)
        if status in (RunStatus.QUEUED.value, RunStatus.HELD.value):
            await finish_run_in(s, ctx, run_id, RunStatus.CANCELLED, reason, now=now)
            if status == RunStatus.HELD.value:  # its workflow waits for a release: wake it
                await emit(
                    s, RunSignalV1(run_id=run_id, kind="cancel", reason=reason), occurred_at=now
                )
        elif status in ACTIVE_RUN:
            await emit(s, RunSignalV1(run_id=run_id, kind="cancel", reason=reason), occurred_at=now)
        return await get_run(s, run_id)


class PostResultIn(tasks.ResultFields):
    """A run's result, from the `post_result` tool, its REST twin or the runner."""

    run_id: UUID


ResultOut = tasks.ResultOut
ResultFields = tasks.ResultFields


async def accept_result(
    s: AsyncSession,
    actor: ActorRef,
    caller_run: UUID | None,
    inp: PostResultIn,
    *,
    now: datetime,
    tainted: bool = False,
) -> ResultOut:
    """Result intake, one path for the tool, its REST twin and the runner (FR-5.8). A
    caller holding a task token (`caller_run`, R-31) posts only for that token's run (403
    `run_mismatch`); a key with no run may post for any run it can reach (the surface's
    write rules, P2-01). A second result for the run answers the first. Otherwise the run
    must be running or waiting (409 `run_not_active`), and in the caller's transaction:
    `tasks.post_result` (the results row, the task In review, `result.posted`), the
    `result` review item and `run.signal{result}`, which ends the run `succeeded`. The
    result is tainted when the caller's write is (`tainted`) or the run is (P2-08, SAF-1)."""
    if caller_run is not None and caller_run != inp.run_id:
        raise ProblemError(403, "run_mismatch", "This token belongs to another run")
    run = (
        await s.execute(
            select(_runs.c.status, _runs.c.task_id, _runs.c.tainted, _runs.c.kind)
            .where(_runs.c.id == inp.run_id, _runs.c.deleted_at.is_(None))
            .with_for_update()
        )
    ).first()
    if run is None:
        raise NotFound("runs", inp.run_id)
    existing = await tasks.result_of_run(s, inp.run_id)
    if existing is not None:
        return existing
    if run.status not in (RunStatus.RUNNING.value, RunStatus.WAITING_ON_HUMAN.value):
        raise ProblemError(409, "run_not_active", f"The run is {run.status}")
    if run.task_id is None:
        raise ProblemError(422, "run_has_no_task", "Only a task's run posts a result")
    fields = tasks.ResultFields.model_validate(inp.model_dump(exclude={"run_id"}))
    fields = await _cite(s, run.task_id, fields)
    stuck = run.kind == RunKind.STUCK.value
    result, created = await tasks.post_result(
        s,
        actor,
        run.task_id,
        inp.run_id,
        fields,
        tainted=tainted or run.tainted,
        now=now,
        keep_status=stuck,  # one step's report: the person is still on the task (P4-02)
    )
    # A stuck run that posted a first step has said everything already: its result only
    # ends the run. One that took the step itself reports back for the person to review.
    review = not stuck or await stuck_reported(s, inp.run_id, fields.summary, now=now)
    if created:
        await emit(s, RunSignalV1(run_id=inp.run_id, kind="result"), occurred_at=now)
    if created and review:
        await tasks.add_review_item(
            RESULT,
            target=tasks.TargetRef(type="task", id=run.task_id),
            project_id=None,
            payload=ResultPayload(run_id=inp.run_id, **fields.model_dump()).model_dump(mode="json"),
            dedupe_key=f"result:{inp.run_id}",
            session=s,
        )
    return result


# --- Stuck handling (P4-02, FR-10.5) ----------------------------------------------------------
#
# "Stuck" on a check-in fires a `focus.event` of kind `stuck`; agents' subscriber starts
# `handle_stuck` (workflows), whose step asks for a stuck run here (`request_stuck_run`) and
# records the request (`stuck_requests`). The run's own calls answer it: the first step it
# posts (`create_task`, through the hooks tasks calls) or its report (`accept_result`). Each
# answer updates the request and emits `stuck.resolved`, whose subscriber wakes the
# workflow (the api never calls DBOS itself). When nothing comes within the deadline, the
# workflow marks the fallback; a late answer still lands and replaces it. The focus bar
# reads the latest request of the day (`next_step`), refreshed over `/ws` (entity `focus`).

STUCK_DEADLINE_S: Final = 60.0  # FR-10.5 "within a minute"; short in tests (R-30)
# DBOS 3.1 dequeues a partition by priority, lowest first, and a workflow enqueued without
# one gets 0, ahead of every prioritised one (Context7 /dbos-inc/dbos-docs, queue tutorial:
# "Workflows without assigned priorities have the highest priority"). So every enqueue in a
# project's partition names one: the stuck run 1, everything else RUN_PRIORITY_NORMAL.
STUCK_PRIORITY: Final = 1
RUN_PRIORITY_NORMAL: Final = 10
STUCK_TIMER_MINUTES: Final = STUCK_MAX_MINUTES  # the fallback's timer (FR-10.5)
STUCK_TOPIC: Final = "stuck_outcome"
LIVE_FOCUS: Final = "focus"  # focus's live entity: the focus bar's one read refreshes
# `done` and `reopened`: the person's review of a `took_step` report (Scott decision 73).
StuckState = Literal["working", "split", "took_step", "fallback", "done", "reopened"]
_stuck_requests: Table = StuckRequest.__table__  # type: ignore[assignment]
_stuck_config: dict[str, float] = {"deadline_s": STUCK_DEADLINE_S}
_OPEN_STUCK: Final = ("working", "fallback")  # an answer still lands (a late one replaces)


def configure_stuck(deadline_seconds: float | None = None) -> None:
    """How long `handle_stuck` waits for the agent's answer (R-30): tests shorten it; no
    argument puts the default back (60 s, `settings.stuck_deadline_seconds`)."""
    if deadline_seconds is not None and deadline_seconds <= 0:
        raise ValueError("the stuck deadline must be positive")
    _stuck_config["deadline_s"] = (
        STUCK_DEADLINE_S if deadline_seconds is None else float(deadline_seconds)
    )


def stuck_deadline_s() -> float:
    return _stuck_config["deadline_s"]


def stuck_workflow_id(focus_event_id: UUID) -> str:
    """`handle_stuck`'s workflow id: one per stuck focus event, so a redelivery or a second
    tap on the same check-in starts nothing new (REL-2)."""
    return f"stuck:{focus_event_id}"


class StuckStart(BaseModel):
    """What `request_stuck_run` did: the run asked for or joined (None when none could
    start), and the request's state."""

    run_id: UUID | None
    state: StuckState


class StuckStepOut(BaseModel):
    task_id: UUID
    title: str
    label: tasks.Label | None
    estimate_minutes: int | None


class NextStepOut(BaseModel):
    """What the focus bar shows after "Stuck" (FR-10.5): `working` while the project agent
    works on a first step; `split` with the subtask it posted (`step`); `took_step` with
    its report (`summary`); `fallback` when no answer came within the deadline or the agent
    is down: the task's `first_action` with a `timer_minutes` timer. Once the person
    reviews a `took_step` report (Scott decision 73): `done` when they accepted it (the
    step is done, the report stays in `summary`), `reopened` when they rejected it (the
    step is theirs again: the task's `first_action`)."""

    focus_event_id: UUID
    task_id: UUID
    run_id: UUID | None
    state: StuckState
    requested_at: datetime
    first_action: str | None
    timer_minutes: int = STUCK_TIMER_MINUTES
    fallback_at: datetime | None
    step: StuckStepOut | None
    summary: str | None


async def _profile_down(s: AsyncSession, run_id: UUID) -> bool:
    """The run's profile's last health check found its agent unreachable (FR-4.6)."""
    health = await s.scalar(
        select(_profiles.c.health)
        .select_from(_runs.join(_profiles, _profiles.c.id == _runs.c.profile_id))
        .where(_runs.c.id == run_id)
    )
    return isinstance(health, dict) and health.get("reachable") is False


async def _active_stuck_run(s: AsyncSession, task_id: UUID) -> UUID | None:
    """The task's stuck run still queued or running, if any."""
    return await s.scalar(
        select(_runs.c.id)
        .where(
            _runs.c.task_id == task_id,
            _runs.c.kind == RunKind.STUCK.value,
            _runs.c.status.in_(ACTIVE_RUN),
        )
        .order_by(_runs.c.created_at.desc())
        .limit(1)
    )


async def request_stuck_run(
    ctx: WorkspaceContext, task_id: UUID, focus_event_id: UUID, *, requested_at: datetime
) -> StuckStart:
    """The stuck run for a stuck focus event (`request_run(task_id, "stuck",
    priority=STUCK_PRIORITY)`, R-23, any label) and its request row, in one transaction.
    Idempotent per focus event: a replay answers the row already there. When a stuck run
    of the task is still active (an earlier check-in's), this request waits on that run's
    answer as well (both requests take it). A run that cannot start (paused, no ready
    agent, a finished task), or an agent whose last health check failed, falls back at
    once (FR-4.6's degraded mode)."""
    async with tenant_session(ctx) as s:
        held = (
            await s.execute(
                select(_stuck_requests.c.run_id, _stuck_requests.c.state).where(
                    _stuck_requests.c.focus_event_id == focus_event_id
                )
            )
        ).first()
        if held is not None:
            return StuckStart(run_id=held.run_id, state=held.state)
        run_id: UUID | None = None
        state: StuckState = "working"
        try:
            async with s.begin_nested():
                run_id = await request_run(
                    task_id,
                    RunKind.STUCK,
                    priority=STUCK_PRIORITY,
                    ctx=ctx,
                    session=s,
                    now=requested_at,
                )
        except NotFound:
            state = "fallback"
        except ProblemError as exc:
            # A stuck run of the task already works on it (an earlier check-in's): this
            # request waits on that run's answer too, or falls back when it has just ended.
            run_id = (
                await _active_stuck_run(s, task_id) if exc.code == "run_already_active" else None
            )
            if run_id is None:
                state = "fallback"
        if run_id is not None and await _profile_down(s, run_id):
            state = "fallback"
        await s.execute(
            pg_insert(_stuck_requests)
            .values(
                focus_event_id=focus_event_id,
                task_id=task_id,
                run_id=run_id,
                state=state,
                requested_at=requested_at,
                fallback_at=requested_at if state == "fallback" else None,
            )
            .on_conflict_do_nothing(index_elements=["workspace_id", "focus_event_id"])
        )
        mark_changed(s, LIVE_FOCUS, focus_event_id)
    return StuckStart(run_id=run_id, state=state)


async def mark_stuck_fallback(
    ctx: WorkspaceContext, focus_event_id: UUID, *, now: datetime
) -> bool:
    """No answer within the deadline: the request still `working` shows the fallback (the
    task's first action with a 10-minute timer); True when it changed."""
    async with tenant_session(ctx) as s:
        changed = await s.scalar(
            update(_stuck_requests)
            .where(
                _stuck_requests.c.focus_event_id == focus_event_id,
                _stuck_requests.c.state == "working",
            )
            .values(state="fallback", fallback_at=now, updated_at=func.now())
            .returning(_stuck_requests.c.id)
        )
        if changed is not None:
            mark_changed(s, LIVE_FOCUS, focus_event_id)
    return changed is not None


async def _stuck_answered(
    s: AsyncSession,
    run_id: UUID,
    outcome: StuckOutcome,
    now: datetime,
    *,
    step_task_id: UUID | None = None,
    summary: str | None = None,
) -> bool:
    """The run's open request (working, or fallen back) takes the answer, in the caller's
    transaction, and `stuck.resolved` wakes its workflow; False when none was open."""
    rows = (
        await s.execute(
            update(_stuck_requests)
            .where(_stuck_requests.c.run_id == run_id, _stuck_requests.c.state.in_(_OPEN_STUCK))
            .values(
                state=outcome,
                step_task_id=step_task_id,
                summary=summary,
                resolved_at=now,
                updated_at=func.now(),
            )
            .returning(_stuck_requests.c.focus_event_id, _stuck_requests.c.task_id)
        )
    ).all()
    for row in rows:
        await emit(
            s,
            StuckResolvedV1(
                focus_event_id=row.focus_event_id,
                task_id=row.task_id,
                run_id=run_id,
                outcome=outcome,
                step_task_id=step_task_id,
            ),
            occurred_at=now,
        )
        mark_changed(s, LIVE_FOCUS, row.focus_event_id)
    return bool(rows)


async def stuck_reported(s: AsyncSession, run_id: UUID, summary: str, *, now: datetime) -> bool:
    """A stuck run's result: it took the step itself and reports back (`took_step`)."""
    return await _stuck_answered(s, run_id, "took_step", now, summary=summary)


async def stuck_result_decided(
    s: AsyncSession,
    actor: ActorRef,
    run_id: UUID,
    decision: str,
    *,
    feedback: str | None,
    now: datetime,
) -> bool:
    """A decided `result` item of run `run_id`, when that run is a stuck run (Scott
    decision 73); False for any other run, whose result the caller applies as usual.

    The step the stuck run took itself is the person's to judge, not the task: accept
    marks it `done` and reject `reopened` (`rules.stuck_step_after`), on every request the
    run answered, and the task's status is left alone (the person is still on it, so the
    focus session carries on). A rejection records its reason as the person's comment on
    the task, and no new run starts: the step is the person's now. Idempotent: a request
    already decided is not touched again, nor is the comment added twice. In the caller's
    transaction."""
    row = (
        await s.execute(select(_runs.c.kind, _runs.c.task_id).where(_runs.c.id == run_id))
    ).first()
    if row is None or row.kind != RunKind.STUCK.value:
        return False
    state = stuck_step_after(decision)
    if state is None:
        return True
    decided = (
        await s.execute(
            update(_stuck_requests)
            .where(_stuck_requests.c.run_id == run_id, _stuck_requests.c.state == "took_step")
            .values(state=state, updated_at=func.now())
            .returning(_stuck_requests.c.focus_event_id)
        )
    ).all()
    if not decided:
        return True  # decided already, or never reported (nothing to judge)
    if state == "reopened" and feedback and row.task_id is not None:
        with contextlib.suppress(NotFound):  # a task gone: the step is reopened all the same
            await tasks.add_comment(s, actor, row.task_id, feedback, now=now)
    for (focus_event_id,) in decided:
        mark_changed(s, LIVE_FOCUS, focus_event_id)
    return True


async def _stuck_run(s: AsyncSession, run_id: UUID) -> tasks.StuckRun | None:
    """The stuck run behind a task token (`tasks.create_task`'s step rule), else None."""
    row = (
        await s.execute(
            select(_runs.c.kind, _runs.c.task_id, _runs.c.tasks_created).where(_runs.c.id == run_id)
        )
    ).first()
    if row is None or row.kind != RunKind.STUCK.value or row.task_id is None:
        return None
    return tasks.StuckRun(task_id=row.task_id, tasks_created=row.tasks_created)


async def _stuck_step_made(
    s: AsyncSession, run_id: UUID, task: tasks.TaskOut, now: datetime
) -> None:
    """A stuck run posted its first step (`split`)."""
    await _stuck_answered(s, run_id, "split", now, step_task_id=task.id)


tasks.register_stuck_run_hooks(_stuck_run, _stuck_step_made)


async def _task_or_none(s: AsyncSession, task_id: UUID | None) -> tasks.TaskOut | None:
    if task_id is None:
        return None
    try:
        return await tasks.get_task(s, task_id)
    except NotFound:
        return None


async def next_step(s: AsyncSession, start: datetime, end: datetime) -> NextStepOut | None:
    """The latest stuck request made in [start, end) (the focus bar's day), as the bar
    shows it; None when there is none."""
    row = (
        await s.execute(
            select(_stuck_requests)
            .where(
                _stuck_requests.c.requested_at >= start,
                _stuck_requests.c.requested_at < end,
                _stuck_requests.c.deleted_at.is_(None),
            )
            .order_by(_stuck_requests.c.requested_at.desc(), _stuck_requests.c.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    stuck_task = await _task_or_none(s, row.task_id)
    step = await _task_or_none(s, row.step_task_id)
    return NextStepOut(
        focus_event_id=row.focus_event_id,
        task_id=row.task_id,
        run_id=row.run_id,
        state=row.state,
        requested_at=row.requested_at,
        first_action=None if stuck_task is None else stuck_task.first_action,
        fallback_at=row.fallback_at,
        step=None
        if step is None
        else StuckStepOut(
            task_id=step.id,
            title=step.title,
            label=step.label,
            estimate_minutes=step.estimate_minutes,
        ),
        summary=row.summary,
    )


LABEL_MAX: Final = 200  # ResultLink.label's bound


async def _cite(s: AsyncSession, task_id: UUID, fields: ResultFields) -> ResultFields:
    """The result's citations checked and labeled (P2-17, FR-15.4): a document link
    `tumnis://doc/<id>#page=<n>` names a live document of the task's project or of the
    workspace knowledge base (else 422 `invalid_citation`, so another project's document
    never leaks into a result), and gets "<title>, page <n>" (or the title) when it has no
    label."""
    if not any(tasks.cited_document(link) for link in fields.links):
        return fields
    task = await tasks.get_task(s, task_id)
    links: list[tasks.ResultLink] = []
    for link in fields.links:
        cited = tasks.cited_document(link)
        if cited is None:
            links.append(link)
            continue
        document_id, page = cited
        title = await knowledge.citation_title(s, document_id, task.project_id)
        if title is None:
            raise ProblemError(
                422,
                "invalid_citation",
                "A citation names a document of the task's project or the workspace's",
            )
        label = link.label
        if label is None:
            label = title if page is None else f"{title}, page {page}"
        links.append(link.labelled(label[:LABEL_MAX]))
    return fields.model_copy(update={"links": links})


# --- The kill switch and runaway limits (P2-09, SAF-4, SAF-5) --------------------------------

_pauses: Table = AgentPause.__table__  # type: ignore[assignment]
PAUSE_AUDIT: Final[dict[str, tuple[str, str]]] = {  # scope -> (pause action, resume action)
    "workspace": ("killswitch.on", "killswitch.off"),
    "project": ("project.paused", "project.resumed"),
}
CANCEL_REASON: Final[dict[str, str]] = {"workspace": KILLSWITCH, "project": PROJECT_PAUSED}
ReasonText = Annotated[str, StringConstraints(min_length=1, max_length=500, pattern=r"\S")]


class PauseIn(BaseModel):
    """Pause every agent (`scope` workspace) or one project's (`scope` project and its
    `project_id`), with the reason the audit log keeps."""

    scope: PauseScope
    project_id: UUID | None = None
    reason: ReasonText


class PauseOut(BaseModel):
    schema_version: Literal[1] = 1
    pause_id: UUID
    cancelled_runs: int  # running or waiting at the time of the request; they end shortly
    held_runs: int  # queued runs now held until a person resumes
    tainted: bool = False  # the pause came through a key with no run (R-31, P2-08)


class ResumeIn(BaseModel):
    scope: PauseScope
    project_id: UUID | None = None
    reason: Annotated[str, StringConstraints(max_length=500)] | None = None


class ResumeOut(BaseModel):
    schema_version: Literal[1] = 1
    pause_id: UUID | None  # None: nothing was paused
    released_runs: int  # held runs nothing holds any more; they start shortly


class OpenPause(BaseModel):
    pause_id: UUID
    scope: PauseScope
    project_id: UUID | None
    reason: str
    paused_at: datetime
    paused_by: str


class PausesOut(BaseModel):
    """What is paused now: the workspace (or None), and each paused project."""

    schema_version: Literal[1] = 1
    workspace: OpenPause | None
    projects: list[OpenPause]


def _open() -> Any:
    return _pauses.c.resumed_at.is_(None) & _pauses.c.deleted_at.is_(None)


def _scope_check(scope: str, project_id: UUID | None) -> None:
    if (scope == "project") != (project_id is not None):
        raise ProblemError(
            422,
            "validation_error",
            "A project pause names its project_id; a workspace pause names none",
        )


async def pause_state_for(s: AsyncSession, project_id: UUID | None) -> PauseState:
    """Whether the project's runs may go on (`rules.pause_state` over the open pauses): read
    by `request_run`, `check_pause`, `prepare_run` and `send_to_agent`."""
    rows = (
        await s.execute(
            select(_pauses.c.scope, _pauses.c.project_id).where(
                _open(),
                (_pauses.c.scope == "workspace") | (_pauses.c.project_id == project_id),
            )
        )
    ).all()
    return pause_state(
        [PauseView(scope=r.scope, project_id=r.project_id) for r in rows], project_id
    )


def _runs_in_scope(scope: str, project_id: UUID | None) -> Any:
    """Runs (with their project) a pause of this scope covers."""
    stmt = select(_runs.c.id).select_from(
        _runs.join(_profiles, _profiles.c.id == _runs.c.profile_id)
    )
    if scope == "project":
        stmt = stmt.where(_profiles.c.project_id == project_id)
    return stmt.where(_runs.c.deleted_at.is_(None))


def dispatched(stmt: Any) -> Any:
    """Runs a workflow of ours supervises: `dispatch_run` runs (its workflow id is the run
    id), the `supervise_run` that took one over after a deploy (P2-05,
    `supervise:<run>:<version>`) and, since Scott decision 40, `run_skill` runs (P1-04
    enrichment and planning, `run_skill:<run>`), whose workflow also stops its agent on a
    `cancel`."""
    run_text = cast(_runs.c.id, Text)
    supervised = _runs.c.workflow_id.startswith("supervise:" + run_text + ":")  # a uuid: no %, _
    skill = _runs.c.workflow_id == "run_skill:" + run_text
    return stmt.where((_runs.c.workflow_id == run_text) | supervised | skill)


async def runs_to_cancel(s: AsyncSession, scope: str, project_id: UUID | None) -> list[UUID]:
    """The running or waiting runs a pause of this scope stops (`dispatch_run` and
    `run_skill` runs alike, `dispatched`)."""
    stmt = _runs_in_scope(scope, project_id).where(
        _runs.c.status.in_((RunStatus.RUNNING.value, RunStatus.WAITING_ON_HUMAN.value))
    )
    return list(await s.scalars(dispatched(stmt).order_by(_runs.c.id)))


async def _hold_queued(s: AsyncSession, scope: str, project_id: UUID | None) -> int:
    """Queued runs in scope -> held (their workflow waits for a release when it starts)."""
    queued = _runs_in_scope(scope, project_id).where(_runs.c.status == RunStatus.QUEUED.value)
    held = list(
        await s.scalars(
            update(_runs)
            # The status again on the target row: under READ COMMITTED a run that
            # prepare_run flipped to running while this waited on its lock is rechecked
            # here, and the subquery's snapshot would not exclude it.
            .where(
                _runs.c.id.in_(queued.scalar_subquery()),
                _runs.c.status == RunStatus.QUEUED.value,
            )
            .values(status=RunStatus.HELD.value, state_seq=_runs.c.state_seq + 1)
            .returning(_runs.c.id)
        )
    )
    for run_id in held:
        mark_changed(s, LIVE_RUN, run_id)
    return len(held)


async def _open_pause(s: AsyncSession, scope: str, project_id: UUID | None) -> RowMapping | None:
    return (
        (
            await s.execute(
                select(_pauses).where(
                    _open(), _pauses.c.scope == scope, _pauses.c.project_id == project_id
                )
                if project_id is not None
                else select(_pauses).where(
                    _open(), _pauses.c.scope == scope, _pauses.c.project_id.is_(None)
                )
            )
        )
        .mappings()
        .first()
    )


async def pause(
    ctx: WorkspaceContext,
    inp: PauseIn,
    *,
    now: datetime,
    session: AsyncSession | None = None,
    tainted: bool = False,
) -> PauseOut:
    """The kill switch (SAF-4), from the app or the master's `pause_agents`: in one
    transaction the pause row, queued runs in scope held, the audit row
    (`killswitch.on`, or `project.paused` naming the project) and `agents.paused`, whose
    subscriber cancels the running and waiting runs in scope through their workflows. A
    scope already paused answers its open pause and changes nothing. `tainted`: a write
    by a key with no run, such as the master's (R-31), kept on the row and answered.
    404 for a project the caller cannot see (before anything else); 422 for a scope and
    project_id that do not match."""
    async with session_for(ctx, session) as s:
        # The project first: an id the caller cannot see is 404 whatever the scope (A0.3).
        if inp.project_id is not None and not await projects.project_exists(s, inp.project_id):
            raise NotFound("projects", inp.project_id)
        _scope_check(inp.scope, inp.project_id)
        existing = await _open_pause(s, inp.scope, inp.project_id)
        if existing is not None:
            return PauseOut(
                pause_id=existing["id"],
                cancelled_runs=0,
                held_runs=0,
                tainted=existing["tainted"],
            )
        pause_id = uuid7()
        try:
            async with s.begin_nested():
                await s.execute(
                    insert(_pauses).values(
                        id=pause_id,
                        scope=inp.scope,
                        project_id=inp.project_id,
                        paused_at=now,
                        paused_by=str(ctx.actor),
                        reason=inp.reason,
                        tainted=tainted,
                    )
                )
        except IntegrityError:  # a concurrent pause of the same scope won
            again = await _open_pause(s, inp.scope, inp.project_id)
            if again is None:
                raise
            return PauseOut(
                pause_id=again["id"], cancelled_runs=0, held_runs=0, tainted=again["tainted"]
            )
        mark_changed(s, LIVE_PAUSE, pause_id)
        held = await _hold_queued(s, inp.scope, inp.project_id)
        cancelled = len(await runs_to_cancel(s, inp.scope, inp.project_id))
        await audit.record(
            s,
            PAUSE_AUDIT[inp.scope][0],
            target=None if inp.project_id is None else ("project", inp.project_id),
            reason=inp.reason,
            details={"pause_id": str(pause_id), "cancelled_runs": cancelled, "held_runs": held},
            occurred_at=now,
        )
        await emit(
            s,
            AgentsPausedV1(
                pause_id=pause_id,
                scope=inp.scope,
                project_id=inp.project_id,
                reason=inp.reason,
                paused_at=now,
            ),
            occurred_at=now,
        )
    return PauseOut(pause_id=pause_id, cancelled_runs=cancelled, held_runs=held, tainted=tainted)


async def held_runs_free(s: AsyncSession, scope: str, project_id: UUID | None) -> list[UUID]:
    """The held runs in scope that no open pause holds any more."""
    rows = (
        await s.execute(
            select(_runs.c.id, _profiles.c.project_id)
            .select_from(_runs.join(_profiles, _profiles.c.id == _runs.c.profile_id))
            .where(
                _runs.c.status == RunStatus.HELD.value,
                _runs.c.deleted_at.is_(None),
                *([_profiles.c.project_id == project_id] if scope == "project" else []),
            )
            .order_by(_runs.c.id)
        )
    ).all()
    return [r.id for r in rows if await pause_state_for(s, r.project_id) == "running"]


async def resume(
    ctx: WorkspaceContext,
    inp: ResumeIn,
    *,
    now: datetime,
    session: AsyncSession | None = None,
) -> ResumeOut:
    """A person lets the agents run again (session only: no key or tool resumes, SAF-4):
    the open pause of the scope resumed, the audit row (`killswitch.off`, or
    `project.resumed`) and `agents.resumed`, whose subscriber releases the held runs no
    other pause holds. Nothing paused: nothing changes (`pause_id` None)."""
    async with session_for(ctx, session) as s:
        # The project first: an id the caller cannot see is 404 whatever the scope (A0.3).
        if inp.project_id is not None and not await projects.project_exists(s, inp.project_id):
            raise NotFound("projects", inp.project_id)
        _scope_check(inp.scope, inp.project_id)
        existing = await _open_pause(s, inp.scope, inp.project_id)
        if existing is None:
            return ResumeOut(pause_id=None, released_runs=0)
        # Still open on the row itself: of two concurrent resumes, the one that waited on
        # the other's row lock finds it resumed and changes nothing (one audit row, one
        # `agents.resumed`).
        resumed = await s.scalar(
            update(_pauses)
            .where(_pauses.c.id == existing["id"], _open())
            .values(resumed_at=now, resumed_by=str(ctx.actor), resume_reason=inp.reason)
            .returning(_pauses.c.id)
        )
        if resumed is None:
            return ResumeOut(pause_id=None, released_runs=0)
        mark_changed(s, LIVE_PAUSE, existing["id"])
        released = len(await held_runs_free(s, inp.scope, inp.project_id))
        await audit.record(
            s,
            PAUSE_AUDIT[inp.scope][1],
            target=None if inp.project_id is None else ("project", inp.project_id),
            reason=inp.reason,
            details={"pause_id": str(existing["id"]), "released_runs": released},
            occurred_at=now,
        )
        await emit(
            s,
            AgentsResumedV1(
                pause_id=existing["id"],
                scope=inp.scope,
                project_id=inp.project_id,
                resumed_at=now,
            ),
            occurred_at=now,
        )
    return ResumeOut(pause_id=existing["id"], released_runs=released)


async def open_pauses(s: AsyncSession) -> PausesOut:
    """What is paused now (the kill switch's state in the app)."""
    rows = (
        (await s.execute(select(_pauses).where(_open()).order_by(_pauses.c.paused_at)))
        .mappings()
        .all()
    )
    found = [
        OpenPause(
            pause_id=r["id"],
            scope=r["scope"],
            project_id=r["project_id"],
            reason=r["reason"],
            paused_at=r["paused_at"],
            paused_by=r["paused_by"],
        )
        for r in rows
    ]
    workspace = next((p for p in found if p.scope == "workspace"), None)
    return PausesOut(workspace=workspace, projects=[p for p in found if p.scope == "project"])


async def count_run_task(s: AsyncSession, run_id: UUID, *, now: datetime) -> None:
    """A task (or subtask) created with a run's task token counts against the run's limit
    (SAF-5): `runs.tasks_created` + 1 in the caller's transaction. Past the project's
    `max_tasks_per_run` (default 20), a separate transaction sends the run
    `run.signal{limit, tasks_per_run}` (its workflow stops the agent and ends the run
    `cancelled` with a `run_limit` review item), and 409 `run_limit_exceeded` rolls the
    caller's transaction back, so the task is not created. A token whose run has no row
    counts nothing. Registered with `tasks.api.register_run_task_counter`."""
    row = (
        await s.execute(
            update(_runs)
            .where(_runs.c.id == run_id)
            .values(tasks_created=_runs.c.tasks_created + 1)
            .returning(_runs.c.workspace_id, _runs.c.tasks_created, _runs.c.status)
        )
    ).first()
    if row is None:
        return
    project_id = await _run_project_id(s, run_id)
    limit = (
        MAX_TASKS_PER_RUN_DEFAULT
        if project_id is None
        else (await projects.get_policy(s, project_id)).max_tasks_per_run
    )
    if not over_task_limit(row.tasks_created, limit):
        return
    if row.status in ACTIVE_RUN:
        # Only an outbox row: the caller's transaction holds the run's row lock.
        async with tenant_session(WorkspaceContext(row.workspace_id, SYSTEM_ACTOR)) as own:
            await emit(
                own,
                RunSignalV1(run_id=run_id, kind="limit", reason=TASKS_PER_RUN),
                occurred_at=now,
            )
    raise ProblemError(
        409,
        "run_limit_exceeded",
        f"This run may create at most {limit} tasks; it has been stopped",
    )


tasks.register_run_task_counter(count_run_task)


# --- Enrichment (P1-08, R-30) ----------------------------------------------------------------

LABEL_WAIT_S_DEFAULT: Final = 10.0  # how long the enrichment waits for a label (plan default)
MIN_RUN_TIMEOUT_S: Final = 10  # TaskPacket.timeout_s's lower bound


class EnrichmentConfig(BaseModel):
    """The enrichment's clock (None: the system clock) and timeouts (R-30)."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    clock: Any = None
    label_wait_s: float = LABEL_WAIT_S_DEFAULT
    run_timeout_s: int = ENRICH_TIMEOUT_S_DEFAULT


_enrichment: list[EnrichmentConfig] = [EnrichmentConfig()]


def configure_enrichment(
    *,
    clock: Any = None,
    label_wait_s: float = LABEL_WAIT_S_DEFAULT,
    run_timeout_s: int = ENRICH_TIMEOUT_S_DEFAULT,
) -> None:
    """The enrichment's clock and timeouts (R-30); called with nothing, the defaults. The
    clock judges the project agent's heartbeats (`agent_for_project`)."""
    if label_wait_s < 0 or run_timeout_s < MIN_RUN_TIMEOUT_S:
        raise ValueError("the label wait is not negative and the run timeout at least 10 s")
    _enrichment[0] = EnrichmentConfig(
        clock=clock, label_wait_s=label_wait_s, run_timeout_s=run_timeout_s
    )


def enrichment_config() -> EnrichmentConfig:
    """The enrichment's current settings (read at each step, never captured at enqueue)."""
    return _enrichment[0]


# --- The master's plan runs (P1-11, FR-4.3) ------------------------------------------------
#
# planning builds the day's plan; agents owns the master profile, the planning packet and
# the run. `master_agent` judges the master the way the daemon transport does before a
# dispatch (the runner's status and inventory), so a master this calls ready is one the
# dispatch accepts; `planning_request` adds what agents knows (project health, briefs,
# the registry) to what planning gathered; `run_plan` runs the packet as a child
# `run_skill` of the caller's workflow through the seam workflows fills at import (api may
# not import workflows).

PLAN_SKILL: Final = "plan"
PLAN_RESULT: Final = SchemaRef(family="planning", name="result", version=1)


class MasterAgentOut(BaseModel):
    availability: AgentAvailability
    profile_id: UUID | None = None
    profile_version: str | None = None  # logged with every plan (PRD risk table)


async def master_agent(*, ctx: WorkspaceContext) -> MasterAgentOut:
    """ready | offline | not_provisioned for the workspace's master profile (the oldest
    live one): not provisioned without one (or while it is provisioning), offline while
    paused, its runner is not online or the runner does not list it; an MCP endpoint
    profile is ready (an unreachable endpoint fails its run instead)."""
    async with tenant_session(ctx) as s:
        found = (
            (
                await s.execute(
                    select(_profiles)
                    .where(_profiles.c.role == "master", _live_profiles())
                    .order_by(_profiles.c.created_at, _profiles.c.id)
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        if found is None:
            return MasterAgentOut(availability="not_provisioned")
        row = _ProfileRow.model_validate(dict(found))
        runner = (
            await s.execute(
                select(_runners.c.status, _runners.c.inventory).where(
                    _runners.c.id == row.runner_id, _runners.c.deleted_at.is_(None)
                )
            )
        ).first()
    out = MasterAgentOut(
        availability="ready", profile_id=row.id, profile_version=row.profile_version
    )
    if row.status in ("provisioning", "not_provisioned"):
        return out.model_copy(update={"availability": "not_provisioned"})
    if row.status == "paused":
        return out.model_copy(update={"availability": "offline"})
    if row.transport == "mcp_endpoint":
        return out
    if runner is None:
        return out.model_copy(update={"availability": "not_provisioned"})
    listed = row.name in {str(p.get("name")) for p in runner.inventory}
    return (
        out
        if runner.status == "online" and listed
        else out.model_copy(update={"availability": "offline"})
    )


async def planning_request(  # the request's parts, spelled out
    ctx: WorkspaceContext,
    *,
    day: date,
    timezone: str,
    now: datetime,
    window: Interval | None,
    free_blocks: Sequence[Interval],
    events: Sequence[tuple[str | None, datetime, datetime]],
    candidates: Sequence[tasks.TaskOut],
) -> PlanningRequest:
    """The `plan` packet's body (P1-05's schema): planning's day and candidates (in its
    order), each candidate project's health, next milestone and brief (`knowledge.api.
    get_brief`; "" before it has one), and the master's registry of project agents."""
    from tumnis.modules.agents.packet_builder import (  # noqa: PLC0415
        PlanningProjectIn,
        assemble_planning_request,
    )
    from tumnis.modules.agents.skill_io import MAX_CANDIDATES  # noqa: PLC0415

    ids = list(dict.fromkeys(task.project_id for task in candidates[:MAX_CANDIDATES]))
    found: list[PlanningProjectIn] = []
    async with tenant_session(ctx) as s:
        if ids:
            page = await projects.list_projects(
                s, project_ids=frozenset(ids), limit=len(ids), now=now
            )
            by_id = {project.id: project for project in page.items}
            for project_id in ids:
                project = by_id.get(project_id)
                if project is None:
                    continue
                try:
                    brief = (await knowledge.get_brief(project_id, session=s)).body_md or ""
                except NotFound:
                    brief = ""
                found.append(
                    PlanningProjectIn(
                        id=project.id,
                        name=project.name,
                        health=project.health,
                        next_milestone=project.next_milestone,
                        brief=brief,
                    )
                )
    registry = await master_registry(ctx=ctx)
    return assemble_planning_request(
        day=day,
        timezone=timezone,
        now=now,
        window=window,
        free_blocks=free_blocks,
        events=events,
        candidates=candidates,
        projects=found,
        agents=registry,
    )


def plan_packet(
    *,
    run_id: UUID,
    profile_id: UUID,
    request: PlanningRequest,
    timeout_s: int,
    correlation_id: str,
) -> TaskPacket:
    """The master's `plan` packet for `request` (R-24): skill `plan`, reply validated
    against planning/result v1."""
    from tumnis.modules.agents.packet_builder import render_prompt  # noqa: PLC0415

    body = request.model_dump(mode="json")
    return TaskPacket(
        kind=RunKind.PLAN,
        run_id=run_id,
        profile_id=profile_id,
        skill=PLAN_SKILL,
        output_schema=PLAN_RESULT,
        correlation_id=correlation_id,
        timeout_s=timeout_s,
        prompt_text=render_prompt(PLAN_SKILL, PLAN_RESULT, body),
        body=body,
    )


class SkillRunner(Protocol):
    """Runs a packet as a child `run_skill` of the calling workflow (`workflows`)."""

    async def __call__(self, workspace_id: UUID, packet: TaskPacket) -> dict[str, Any]: ...


_skill_runner: list[SkillRunner] = []


def register_skill_runner(runner: SkillRunner) -> None:
    """workflows registers `run_skill` at import (the provision starter's pattern)."""
    _skill_runner[:] = [runner]


async def run_plan(workspace_id: UUID, packet: TaskPacket) -> RunOutcome:
    """Run the plan packet from inside the caller's DBOS workflow: a child `run_skill`
    with workflow id `run_skill:<run id>`, so a replayed caller dispatches nothing twice."""
    if not _skill_runner:
        raise RuntimeError("agents.workflows is not loaded: nothing can run a skill")
    return RunOutcome.model_validate(await _skill_runner[0](workspace_id, packet))


# --- The master's notify runs (P2-16, FR-8.2) ---------------------------------------------
#
# notifications decides what reaches the person and when; agents owns the master profile,
# the notify packet and the run. The master's `focus` skill words the message and posts it
# to the one Discord channel itself (its gateway; the credentials live only in Hermes,
# design decision 7), then replies with the message. The run's task token is workspace
# scoped with `tasks:read` only (Scott decision 30, `RUN_TOKEN_SCOPES`).

NOTIFY_SKILL: Final = "focus"
NOTIFY_RESULT: Final = SchemaRef(family="result", name="focus_message", version=1)
NOTIFY_TIMEOUT_S: Final = 60  # the plan's notify run cap


def notify_packet(
    *,
    run_id: UUID,
    profile_id: UUID,
    request: NotifyRequest,
    correlation_id: str,
    tainted: bool = False,
    timeout_s: int = NOTIFY_TIMEOUT_S,
) -> TaskPacket:
    """The master's `notify` packet for `request` (R-24): skill `focus`, reply validated
    against result/focus_message v1. `tainted` when a text it carries came from outside
    (P2-08), so the run is tainted and every gated action refused (SAF-1)."""
    from tumnis.modules.agents.packet_builder import render_prompt  # noqa: PLC0415

    body = request.model_dump(mode="json")
    return TaskPacket(
        kind=RunKind.NOTIFY,
        run_id=run_id,
        profile_id=profile_id,
        skill=NOTIFY_SKILL,
        output_schema=NOTIFY_RESULT,
        correlation_id=correlation_id,
        timeout_s=timeout_s,
        prompt_text=render_prompt(NOTIFY_SKILL, NOTIFY_RESULT, body),
        body=body,
        tainted=tainted,
    )


async def run_notify(workspace_id: UUID, packet: TaskPacket) -> RunOutcome:
    """Run the notify packet from inside the caller's DBOS workflow, as `run_plan` does: a
    child `run_skill` with workflow id `run_skill:<run id>`."""
    return await run_plan(workspace_id, packet)


# --- Focus activity (P2-15) ---------------------------------------------------------------


async def task_activity_times(
    ctx: WorkspaceContext, task_id: UUID, *, since: datetime, until: datetime
) -> list[datetime]:
    """When the task's runs streamed events inside [since, until], oldest first: the agent
    activity that suppresses a focus check-in (FR-10.7b)."""
    stmt = (
        select(_events.c.created_at)
        .join(_runs, _runs.c.id == _events.c.run_id)
        .where(
            _runs.c.task_id == task_id,
            _events.c.created_at >= since,
            _events.c.created_at <= until,
        )
        .order_by(_events.c.created_at)
    )
    async with tenant_session(ctx) as s:
        found: list[datetime] = list((await s.scalars(stmt)).all())
    return found


# --- Close the day (P1-18) ----------------------------------------------------------------------


class FinishedRunOut(BaseModel):
    """A run that ended, as the close-the-day panel counts it (planning, P1-18)."""

    run_id: UUID
    task_id: UUID | None
    kind: str
    status: RunStatus
    finished_at: datetime


async def finished_runs(s: AsyncSession, start: datetime, end: datetime) -> list[FinishedRunOut]:
    """The workspace's runs (in context) that finished within [start, end), in finish
    order: one statement."""
    rows = await s.execute(
        select(_runs.c.id, _runs.c.task_id, _runs.c.kind, _runs.c.status, _runs.c.finished_at)
        .where(
            _runs.c.deleted_at.is_(None),
            _runs.c.finished_at >= start,
            _runs.c.finished_at < end,
        )
        .order_by(_runs.c.finished_at, _runs.c.id)
    )
    return [
        FinishedRunOut(
            run_id=row.id,
            task_id=row.task_id,
            kind=row.kind,
            status=RunStatus(row.status),
            finished_at=row.finished_at,
        )
        for row in rows
    ]


# --- Ask the agent, a project's Activity and the dashboard feed (P2-17, FR-2.5, FR-2.6, FR-1.5)
#
# Asking keeps the conversation inside a task (PRD non-goal: no general chat): the question
# becomes an AI task of the project, run at once on the project's agent (R-23); the run's
# result is the answer. A project's Activity merges its task runs, their results and the
# audit rows written for it, newest first; the dashboard feed groups the workspace's task
# runs by what the human cares about.

ASK_FIRST_ACTION: Final = "Answer the question"
ASK_CRITERIA: Final = "An answer with sources"
ASK_SOURCE: Final = "ask"  # tasks.source of a task made by Ask the agent
TITLE_MAX: Final = 500  # tasks.Title's bound


class AskIn(BaseModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
    ]


class AskOut(BaseModel):
    """The task the question became, and the run answering it."""

    task: tasks.TaskOut
    run_id: UUID


async def ask(  # the question, plus who, where and when
    s: AsyncSession,
    actor: ActorRef,
    project_id: UUID,
    question: str,
    *,
    ctx: WorkspaceContext,
    now: datetime,
) -> AskOut:
    """Ask the project's agent (FR-2.5) in the caller's transaction: an AI task (source
    `ask`, the question as its title, cut with an ellipsis past 500 characters and then
    kept whole in a comment), then `request_run` of it. 404 for a project the caller cannot
    see; 409 `no_ready_profile` when the project has no ready agent, which rolls the task
    back with the transaction."""
    title = question if len(question) <= TITLE_MAX else question[: TITLE_MAX - 1].rstrip() + "…"
    task = await tasks.create_task(
        s,
        actor,
        tasks.TaskCreate(
            project_id=project_id,
            title=title,
            label="ai",
            first_action=ASK_FIRST_ACTION,
            acceptance_criteria=ASK_CRITERIA,
        ),
        now=now,
        source=ASK_SOURCE,
    )
    if title != question:
        await tasks.add_comment(s, actor, task.id, question, now=now)
    run_id = await request_run(task.id, RunKind.TASK, ctx=ctx, session=s, now=now)
    return AskOut(task=task, run_id=run_id)


ActivityKind = Literal["run", "result", "audit"]
ACTIVITY_RUN_KINDS: Final = (RunKind.TASK.value,)  # runs a person asked for, not enrichment


class ActivityItem(BaseModel):
    """One row of a project's Activity: a run (its status now), a result (its summary) or
    an audit row (its action and who did it), at the time it happened."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    kind: ActivityKind
    id: UUID  # the run's, the result's or the audit row's id
    at: datetime
    task_id: UUID | None = None
    task_title: str | None = None
    run_id: UUID | None = None
    status: RunStatus | None = None  # a run's
    summary: str | None = None  # a result's
    action: str | None = None  # an audit row's
    actor_type: str | None = None  # an audit row's


def _uuid_or_none(value: Any) -> UUID | None:
    try:
        return UUID(str(value)) if value is not None else None
    except ValueError:
        return None


async def _activity_runs(
    s: AsyncSession, project_id: UUID, before: tuple[datetime, UUID] | None, limit: int
) -> list[ActivityItem]:
    stmt = (
        select(_runs.c.id, _runs.c.created_at, _runs.c.task_id, _runs.c.status)
        .select_from(_runs.join(_profiles, _profiles.c.id == _runs.c.profile_id))
        .where(
            _profiles.c.project_id == project_id,
            _runs.c.kind.in_(ACTIVITY_RUN_KINDS),
            _runs.c.deleted_at.is_(None),
        )
    )
    if before is not None:
        bound = (literal(before[0], _runs.c.created_at.type), literal(before[1], _runs.c.id.type))
        stmt = stmt.where(tuple_(_runs.c.created_at, _runs.c.id) < tuple_(*bound))
    stmt = stmt.order_by(_runs.c.created_at.desc(), _runs.c.id.desc()).limit(limit)
    return [
        ActivityItem(
            kind="run",
            id=row.id,
            at=row.created_at,
            task_id=row.task_id,
            run_id=row.id,
            status=RunStatus(row.status),
        )
        for row in await s.execute(stmt)
    ]


async def activity(
    s: AsyncSession, project_id: UUID, *, cursor: str | None, limit: int
) -> Page[ActivityItem]:
    """The project's Activity (FR-2.6): its task runs, their results and the audit rows
    written for it (`audit.record(..., project_id=)`), newest first by (at, id), a page
    at a time. Each source reads at most `limit + 1` rows past the cursor, so a page costs
    four statements however busy the project. 404 for a project the caller cannot see;
    400 `invalid_cursor`."""
    if not await projects.project_exists(s, project_id):
        raise NotFound("projects", project_id)
    before: tuple[datetime, UUID] | None = None
    if cursor is not None:
        after = Cursor.decode(cursor, [datetime])
        before = (after.keys[0], after.id)
    items = await _activity_runs(s, project_id, before, limit + 1)
    items += [
        ActivityItem(
            kind="result",
            id=r.id,
            at=r.created_at,
            task_id=r.task_id,
            run_id=r.run_id,
            summary=r.summary,
        )
        for r in await tasks.results_for_project(s, project_id, before=before, limit=limit + 1)
    ]
    items += [
        ActivityItem(
            kind="audit",
            id=a.id,
            at=a.occurred_at,
            run_id=_uuid_or_none(a.details.get("run_id")),
            action=a.action,
            actor_type=a.actor_type,
        )
        for a in await audit.list_for_project(s, project_id, before=before, limit=limit + 1)
    ]
    items.sort(key=lambda item: (item.at, item.id), reverse=True)
    page = items[:limit]
    titles = {
        t.id: t.title for t in await tasks.tasks_by_ids(s, {i.task_id for i in page if i.task_id})
    }
    page = [
        i.model_copy(update={"task_title": titles.get(i.task_id)}) if i.task_id else i for i in page
    ]
    next_cursor = Cursor((page[-1].at,), page[-1].id).encode() if len(items) > limit else None
    return Page[ActivityItem](items=page, next_cursor=next_cursor)


FEED_SIZE: Final = 10  # entries per group (plan default)
FEED_GROUPS: Final[dict[str, frozenset[RunStatus]]] = {
    "running": frozenset({RunStatus.QUEUED, RunStatus.RUNNING}),
    "waiting": frozenset({RunStatus.WAITING_ON_HUMAN, RunStatus.HELD}),
    "finished": frozenset({RunStatus.SUCCEEDED}),
    "failed": frozenset({RunStatus.FAILED, RunStatus.TIMED_OUT, RunStatus.RUNNER_LOST}),
}  # a cancelled run is the human's own doing, so it shows in none


class FeedRun(BaseModel):
    run_id: UUID
    task_id: UUID | None
    task_title: str | None
    project_id: UUID | None
    kind: RunKind
    status: RunStatus
    at: datetime  # when it finished, else started, else was asked for


class AgentFeedOut(BaseModel):
    """The dashboard's agent activity (FR-1.5): the newest task runs in each group."""

    running: list[FeedRun]
    waiting: list[FeedRun]
    finished: list[FeedRun]
    failed: list[FeedRun]


async def agent_feed(s: AsyncSession) -> AgentFeedOut:
    """The workspace's task runs by group, the newest `FEED_SIZE` of each: one statement
    per group and one for the task titles."""
    at = func.coalesce(_runs.c.finished_at, _runs.c.started_at, _runs.c.created_at).label("at")
    groups: dict[str, list[FeedRun]] = {}
    for name, statuses in FEED_GROUPS.items():
        rows = await s.execute(
            select(
                _runs.c.id,
                _runs.c.task_id,
                _runs.c.kind,
                _runs.c.status,
                at,
                _profiles.c.project_id,
            )
            .select_from(_runs.join(_profiles, _profiles.c.id == _runs.c.profile_id))
            .where(
                _runs.c.kind.in_(ACTIVITY_RUN_KINDS),
                _runs.c.status.in_([status.value for status in statuses]),
                _runs.c.deleted_at.is_(None),
            )
            .order_by(at.desc(), _runs.c.id.desc())
            .limit(FEED_SIZE)
        )
        groups[name] = [
            FeedRun(
                run_id=row.id,
                task_id=row.task_id,
                task_title=None,
                project_id=row.project_id,
                kind=RunKind(row.kind),
                status=RunStatus(row.status),
                at=row.at,
            )
            for row in rows
        ]
    wanted = {run.task_id for runs in groups.values() for run in runs if run.task_id}
    titles: dict[UUID | None, str] = {
        t.id: t.title for t in await tasks.tasks_by_ids(s, {w for w in wanted if w})
    }
    return AgentFeedOut(
        **{
            name: [r.model_copy(update={"task_title": titles.get(r.task_id)}) for r in runs]
            for name, runs in groups.items()
        }
    )


# --- Seed writers (the acceptance seed, Scott decision 37) -----------------------------------


def _seed_ctx(workspace_id: UUID) -> WorkspaceContext:
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

    return WorkspaceContext(workspace_id, SYSTEM_ACTOR)


def _seed_secrets_ready() -> None:
    """A runner's device token and an agent's key are HMACs with the pepper: without one
    the record is skipped (SeedWriterUnavailableError), like the seed user without a
    master key."""
    from tumnis.core import crypto  # noqa: PLC0415

    try:
        crypto.peppers()
    except crypto.MasterKeyError as exc:
        raise SeedWriterUnavailableError(f"seed runner or agent not stored: {exc}") from exc


async def seed_runner(workspace_id: UUID, rec: RunnerSeed) -> UUID:
    """A seed runner, made the way Settings makes one. Its device token (shown once) is
    dropped: a test that connects as the runner rotates it (tests/fakes/fake_runner.py)."""
    from tumnis.core.clock import SystemClock  # noqa: PLC0415

    _seed_secrets_ready()
    ctx = _seed_ctx(workspace_id)
    async with tenant_session(ctx) as s:
        created = await create_runner(ctx, s, RunnerIn(name=rec.name), now=SystemClock().now())
    return created.id


async def seed_agent(
    workspace_id: UUID, runner_id: UUID | None, project_id: UUID | None, rec: AgentSeed
) -> UUID:
    """A seed agent profile on its runner (transport `daemon`). A project agent is written
    provisioned (`ready`), so the `project.created` relay of its project leaves it alone
    (P1-06). With `key_scopes`, a key of those scopes is made and linked as the key the
    profile's runs issue task tokens from (P2-02); the key is generated here, never read
    from the seed file."""
    from tumnis.core.clock import SystemClock  # noqa: PLC0415

    _seed_secrets_ready()
    ctx, now = _seed_ctx(workspace_id), SystemClock().now()
    body = ProfileIn(
        name=rec.name,
        role=rec.role,
        transport="daemon",
        runner_id=runner_id,
        project_id=project_id,
    )
    async with tenant_session(ctx) as s:
        profile = await register_profile(s, body, now=now)
        if rec.role == "project":
            await s.execute(
                update(_profiles).where(_profiles.c.id == profile.id).values(status="ready")
            )
            mark_changed(s, LIVE_PROFILE, profile.id)
    if rec.key_scopes:
        key = await auth.create_key(
            ctx, auth.KeyIn(name=f"{rec.name} key", scopes=list(rec.key_scopes)), now=now
        )
        await set_profile_key(ctx, profile.id, key.id, now=now)
    return profile.id


register_seed_writer("runner", seed_runner)
register_seed_writer("agent", seed_agent)
