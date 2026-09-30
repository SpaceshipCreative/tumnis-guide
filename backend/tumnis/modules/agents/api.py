"""agents public functions and DTOs; the only file other modules may import (P1-04).

The one agent interface (`AgentAdapter`, FR-14.6) and the run vocabulary (R-22), runners
(the daemons that dial in over `/ws/runner` with a device token) and agent profiles (the
Hermes profiles Tumnis may run: one master, one per project).
"""

import json
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal, Protocol
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import Table, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import audit
from tumnis.core.errors import ProblemError
from tumnis.core.live import mark_changed
from tumnis.core.pagination import Page, SortKey, paginate
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.versioning import NotFound, Version, update_versioned
from tumnis.modules.agents.adapters.port import (
    AgentAdapter,
    AgentCapabilities,
    AgentHealth,
    AgentUnavailable,
    RunEvent,
    RunHandle,
)
from tumnis.modules.agents.models import AgentProfile, RunEventRow, Runner
from tumnis.modules.agents.packet_builder import TaskPacket
from tumnis.modules.agents.protocol import McpServerInfo, SchemaRef
from tumnis.modules.agents.review_kinds import ForeignReach
from tumnis.modules.agents.rules import (
    NAME_RE,
    TERMINAL_STATUSES,
    InvalidProfileName,
    RunKind,
    RunnerStatus,
    RunStatus,
    TokenReach,
    allowlist_drift,
    runner_status,
    validate_profile_name,
)
from tumnis.modules.agents.skill_io import (
    EnrichmentRequest,
    EnrichmentResult,
    PlanningRequest,
    PlanningResult,
    ProjectAgentEntry,
)
from tumnis.modules.auth import api as auth
from tumnis.modules.projects import api as projects
from tumnis.modules.tasks import api as tasks

if TYPE_CHECKING:
    from dbos import DBOSClient

__all__ = [
    "AgentAdapter",
    "AgentAvailability",
    "AgentCapabilities",
    "AgentHealth",
    "AgentProfileOut",
    "AgentUnavailable",
    "EnrichmentRequest",
    "EnrichmentResult",
    "ForeignReach",
    "HealthCheckAccepted",
    "McpServerInfo",
    "PlanningRequest",
    "PlanningResult",
    "ProfileIn",
    "ProfilePatch",
    "ProfileToolsOut",
    "ProjectAgentEntry",
    "RunEvent",
    "RunHandle",
    "RunKind",
    "RunOutcome",
    "RunStatus",
    "RunnerCreated",
    "RunnerIn",
    "RunnerOut",
    "SchemaRef",
    "TaskPacket",
    "TokenReach",
    "ToolServerOut",
    "retry_provision",
    "run_log",
]

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
TEMPLATE_VERSION: Final = "1.0.0"  # profiles/project-template/VERSION (a unit test holds them)
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


# --- Enrichment (P1-08): red-phase seam; the spec tests turn it green ----------------------


def configure_enrichment(
    *, clock: Any = None, label_wait_s: float = 10.0, run_timeout_s: int = 120
) -> None:
    """The enrichment's clock and timeouts (R-30); called with nothing, the defaults."""
    raise NotImplementedError("P1-08")
