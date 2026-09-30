"""Runner protocol, version 1 (P1-04, R-25): the daemon's own copies of the messages.

The server's models live in `backend/tumnis/modules/agents/protocol.py`; the daemon never
imports the backend. Its contract test (T-P1-04-03) validates every message built here, and
every server message parsed here, against the committed JSON Schemas in
`schemas/runner/v1/`, so the two copies cannot drift. All builders live in this module.
"""

import json
from datetime import UTC, datetime
from typing import Annotated, Any, Final, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

NAME_RE: Final = r"^[a-z0-9][a-z0-9-]{0,62}$"  # profile and runner names
SKILL_RE: Final = r"^[a-z][a-z0-9-]{0,40}$"
PROTOCOL_VERSIONS: Final = (1,)  # what this daemon speaks
TEXT_MAX: Final = 65_536
ERROR_MAX: Final = 4_096
MAX_REACH_TARGETS: Final = 50  # repos or apps probed per kind (plan default)


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Envelope(_Model):
    schema_version: Literal[1] = 1
    message_id: UUID
    correlation_id: str = Field(max_length=128)
    sent_at: datetime


class SchemaRef(_Model):
    family: str = Field(max_length=32)
    name: str = Field(max_length=64)
    version: int = Field(ge=1)


class ProfileInfo(_Model):
    name: str = Field(pattern=NAME_RE)
    distribution_name: str | None = Field(default=None, max_length=128)
    distribution_version: str | None = Field(default=None, max_length=64)


# --- daemon -> server ------------------------------------------------------------------


class Register(Envelope):
    type: Literal["register"] = "register"
    protocol_versions: list[int] = Field(min_length=1, max_length=16)
    runner_name: str = Field(pattern=NAME_RE)
    host: str = Field(max_length=253)
    os: Literal["linux", "darwin"]
    daemon_version: str = Field(max_length=64)
    hermes_version: str | None = Field(max_length=64)
    profiles: list[ProfileInfo] = Field(max_length=500)
    capabilities: list[Literal["run", "provision", "health"]]
    running_run_ids: list[UUID] = Field(default=[], max_length=500)


class Heartbeat(Envelope):
    type: Literal["heartbeat"] = "heartbeat"
    seq: int = Field(ge=0)
    running_run_ids: list[UUID] = Field(default=[], max_length=500)


class Result(Envelope):
    type: Literal["result"] = "result"
    run_id: UUID
    status: Literal["succeeded", "failed", "timed_out"]
    exit_code: int | None
    output_json: dict[str, Any] | None
    text: str = Field(max_length=TEXT_MAX)
    error: str | None = Field(default=None, max_length=ERROR_MAX)
    duration_ms: int = Field(ge=0)
    tokens: dict[str, int] | None = None
    hermes_session_id: str | None = Field(default=None, max_length=256)


class McpServerInfo(_Model):
    """One MCP server of a profile: its name, transport and a redacted target (the
    command's name, or the URL's host); never its arguments, env or headers (P2-10)."""

    name: str = Field(max_length=128)
    transport: Literal["stdio", "http"]
    target: str | None = Field(default=None, max_length=253)


class TokenReach(_Model):
    """What one token reaches, probed on the host; the token never leaves it (P2-10)."""

    token_present: bool
    own_reachable: dict[str, bool] = Field(default={}, max_length=MAX_REACH_TARGETS)
    foreign_reachable: list[str] = Field(default=[], max_length=MAX_REACH_TARGETS)
    errors: list[Annotated[str, Field(max_length=300)]] = Field(default=[], max_length=100)


class HealthReport(Envelope):
    type: Literal["health_report"] = "health_report"
    request_id: UUID
    profile: str = Field(pattern=NAME_RE)
    profile_exists: bool
    reachable: bool
    authenticated: bool | None
    hermes_version: str | None = Field(max_length=64)
    mcp_servers: list[str] = Field(default=[], max_length=200)
    error: str | None = Field(default=None, max_length=ERROR_MAX)
    # P2-10; defaults keep a report without them valid
    mcp_server_details: list[McpServerInfo] = Field(default=[], max_length=200)
    profile_version: str | None = Field(default=None, max_length=64)
    github: TokenReach | None = None
    coolify: TokenReach | None = None


class Ack(Envelope):
    type: Literal["ack"] = "ack"
    ack_of: UUID


class ProvisionResult(Envelope):
    type: Literal["provision_result"] = "provision_result"
    request_id: UUID
    profile: str = Field(pattern=NAME_RE)
    status: Literal["created", "exists", "linked", "failed"]
    distribution_version: str | None = Field(max_length=64)
    error_code: (
        Literal["template_version_mismatch", "not_found", "hermes_error", "invalid_name"] | None
    )
    error: str | None = Field(default=None, max_length=ERROR_MAX)


# --- server -> daemon ------------------------------------------------------------------


class Registered(Envelope):
    type: Literal["registered"] = "registered"
    runner_id: UUID
    protocol_version: int
    heartbeat_interval_s: Literal[15] = 15


class Run(Envelope):
    type: Literal["run"] = "run"
    run_id: UUID
    profile: str = Field(pattern=NAME_RE)
    skill: str = Field(pattern=SKILL_RE)
    packet: dict[str, Any]
    output_schema: SchemaRef
    timeout_s: int = Field(ge=10, le=3600)
    workdir_policy: Literal["none", "worktree"] = "none"


class HealthCheck(Envelope):
    type: Literal["health_check"] = "health_check"
    request_id: UUID
    profile: str = Field(pattern=NAME_RE)
    # P2-10: what the token reach probes look at ("owner/name" repos, Coolify app UUIDs)
    own_repos: list[str] = Field(default=[], max_length=MAX_REACH_TARGETS)
    foreign_repos: list[str] = Field(default=[], max_length=MAX_REACH_TARGETS)
    own_apps: list[str] = Field(default=[], max_length=MAX_REACH_TARGETS)
    foreign_apps: list[str] = Field(default=[], max_length=MAX_REACH_TARGETS)
    # compared with the profile's own COOLIFY_BASE_URL; never a token destination (decision 18)
    coolify_base_url: str | None = Field(default=None, max_length=2048)


class Provision(Envelope):
    type: Literal["provision"] = "provision"
    request_id: UUID
    profile: str = Field(pattern=NAME_RE)
    mode: Literal["create", "link"]
    template: Literal["project-template"]
    template_version: str = Field(max_length=64)


class ProtocolError(Envelope):
    type: Literal["error"] = "error"
    code: Literal[
        "unsupported_schema_version",
        "unsupported_protocol_version",
        "invalid_message",
        "unknown_runner",
        "not_registered",
    ]
    detail: str = Field(max_length=ERROR_MAX)


DaemonMessage = Annotated[
    Register | Heartbeat | Result | HealthReport | ProvisionResult | Ack,
    Field(discriminator="type"),
]
ServerMessage = Annotated[
    Registered | Run | HealthCheck | Provision | Ack | ProtocolError,
    Field(discriminator="type"),
]
_SERVER: Final[TypeAdapter[ServerMessage]] = TypeAdapter(ServerMessage)


class InvalidFrame(ValueError):  # noqa: N818  # the protocol's word
    """A server frame that is not a protocol 1 message."""


def parse_server(raw: str | bytes) -> ServerMessage:
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise InvalidFrame("frame is not JSON") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise InvalidFrame("unsupported schema_version")
    try:
        return _SERVER.validate_python(data)
    except ValidationError as exc:
        raise InvalidFrame(f"invalid message: {exc.error_count()} errors") from exc


# --- builders --------------------------------------------------------------------------


def now() -> datetime:
    return datetime.now(UTC)


def envelope(correlation_id: str) -> dict[str, Any]:
    """A fresh message id, the correlation id and the send time."""
    return {"message_id": uuid4(), "correlation_id": correlation_id, "sent_at": now()}


def make_register(
    *,
    runner_name: str,
    host: str,
    os: Literal["linux", "darwin"],
    daemon_version: str,
    hermes_version: str | None,
    profiles: list[str],
    running_run_ids: list[UUID],
) -> Register:
    return Register(
        **envelope(f"runner:{runner_name}"),
        protocol_versions=list(PROTOCOL_VERSIONS),
        runner_name=runner_name,
        host=host[:253],
        os=os,
        daemon_version=daemon_version,
        hermes_version=hermes_version,
        profiles=[ProfileInfo(name=p) for p in profiles],
        capabilities=["run", "health"],
        running_run_ids=running_run_ids,
    )


def make_heartbeat(runner_name: str, seq: int, running_run_ids: list[UUID]) -> Heartbeat:
    return Heartbeat(**envelope(f"runner:{runner_name}"), seq=seq, running_run_ids=running_run_ids)


def make_ack(message: Envelope) -> Ack:
    return Ack(**envelope(message.correlation_id), ack_of=message.message_id)
