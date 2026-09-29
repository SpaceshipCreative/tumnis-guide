"""agents public functions and DTOs; the only file other modules may import (P1-04).

The one agent interface (`AgentAdapter`, FR-14.6) and the run vocabulary (R-22), runners
(the daemons that dial in over `/ws/runner` with a device token) and agent profiles (the
Hermes profiles Tumnis may run: one master, one per project).
"""

from datetime import datetime
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.pagination import Page
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.versioning import Version
from tumnis.modules.agents.adapters.port import (
    AgentAdapter,
    AgentCapabilities,
    AgentHealth,
    AgentUnavailable,
    RunEvent,
    RunHandle,
)
from tumnis.modules.agents.packet_builder import TaskPacket
from tumnis.modules.agents.protocol import SchemaRef
from tumnis.modules.agents.rules import (
    NAME_RE,
    TERMINAL_STATUSES,
    RunKind,
    RunnerStatus,
    RunStatus,
)

__all__ = [
    "AgentAdapter",
    "AgentAvailability",
    "AgentCapabilities",
    "AgentHealth",
    "AgentProfileOut",
    "AgentUnavailable",
    "HealthCheckAccepted",
    "ProfileIn",
    "ProfilePatch",
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
]

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


class ProfileHealth(BaseModel):
    reachable: bool
    authenticated: bool | None = None
    version: str | None = None
    profile_exists: bool | None = None
    mcp_servers: list[str] = []
    error: str | None = None
    status: Literal["ok", "offline", "unsupported", "error"] = "ok"


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
    status: Literal["succeeded", "failed", "timed_out", "runner_lost"]
    output_json: dict[str, Any] | None = None
    error: str | None = None


# --- Runners (session only, admin) ---------------------------------------------------------


async def list_runners(
    s: AsyncSession, *, now: datetime, cursor: str | None, limit: int
) -> Page[RunnerOut]:
    raise NotImplementedError(f"P1-04 {s} {now} {cursor} {limit}")


async def create_runner(
    ctx: WorkspaceContext, s: AsyncSession, body: RunnerIn, *, now: datetime
) -> RunnerCreated:
    raise NotImplementedError(f"P1-04 {ctx} {s} {body} {now}")


async def rotate_runner_token(
    ctx: WorkspaceContext, s: AsyncSession, runner_id: UUID, *, now: datetime
) -> RunnerCreated:
    raise NotImplementedError(f"P1-04 {ctx} {s} {runner_id} {now}")


# --- Profiles ----------------------------------------------------------------------------


async def list_profiles(
    s: AsyncSession, *, cursor: str | None, limit: int
) -> Page[AgentProfileOut]:
    raise NotImplementedError(f"P1-04 {s} {cursor} {limit}")


async def register_profile(s: AsyncSession, body: ProfileIn, *, now: datetime) -> AgentProfileOut:
    raise NotImplementedError(f"P1-04 {s} {body} {now}")


async def update_profile(
    s: AsyncSession, profile_id: UUID, body: ProfilePatch, *, now: datetime
) -> AgentProfileOut:
    raise NotImplementedError(f"P1-04 {s} {profile_id} {body} {now}")


async def request_health_check(
    ctx: WorkspaceContext, s: AsyncSession, profile_id: UUID, *, now: datetime
) -> HealthCheckAccepted:
    raise NotImplementedError(f"P1-04 {ctx} {s} {profile_id} {now}")


async def profile_health(ctx: WorkspaceContext, profile_id: UUID) -> ProfileHealth | None:
    raise NotImplementedError(f"P1-04 {ctx} {profile_id}")


# --- The agent for a profile or a project --------------------------------------------------


async def adapter_for(profile_id: UUID, *, ctx: WorkspaceContext | None = None) -> AgentAdapter:
    """The AgentAdapter of the profile: HermesAgent over its transport, or the FakeAgent
    in fakes mode while no runner has ever connected."""
    raise NotImplementedError(f"P1-04 {profile_id} {ctx}")


async def agent_for_project(
    project_id: UUID, *, now: datetime, ctx: WorkspaceContext | None = None
) -> AgentAvailability:
    """ready | offline | not_provisioned for the project's agent profile."""
    raise NotImplementedError(f"P1-04 {project_id} {now} {ctx}")
