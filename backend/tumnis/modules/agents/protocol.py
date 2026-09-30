"""Runner protocol, version 1 (P1-04, R-25, FR-5.11): the messages the runner daemon and
the api exchange over `/ws/runner`, one JSON text frame each.

Every message is a `VersionedPayload` with an integer `schema_version` (R-02), registered
as `@versioned("runner", "<type>", 1)`: `make gen` writes `schemas/runner/v1/<type>.json`,
which the daemon's own copies of these models are checked against (its contract test), so
the two sides cannot drift. `register` lists the protocol versions the daemon speaks; the
server answers `registered` with the highest version both support, or
`error{unsupported_protocol_version}` and a close. Protocol 1 acknowledges every message,
in either direction, with its own `ack{ack_of}`; protocol 2 (P2-07) adds batched acks,
`stream`, `cancel`, `upload_artifact`, `archive` and `status`.
"""

import json
from collections.abc import Iterable
from datetime import datetime
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from tumnis.core.schemas import VersionedPayload, versioned
from tumnis.modules.agents.rules import MAX_REACH_TARGETS, NAME_RE, SKILL_RE, TokenReach

__all__ = [
    "NAME_RE",
    "SERVER_PROTOCOL_VERSIONS",
    "Ack",
    "DaemonMessage",
    "Envelope",
    "HealthCheck",
    "HealthReport",
    "Heartbeat",
    "InvalidMessage",
    "McpServerInfo",
    "ProfileInfo",
    "ProtocolError",
    "Provision",
    "ProvisionResult",
    "Register",
    "Registered",
    "Result",
    "Run",
    "SchemaRef",
    "ServerMessage",
    "TokenReach",
    "negotiate",
    "parse_daemon",
    "parse_server",
]

SERVER_PROTOCOL_VERSIONS: Final = (1,)  # P2-07 adds 2
TEXT_MAX: Final = 65_536
ERROR_MAX: Final = 4_096

ErrorCode = Literal[
    "unsupported_schema_version",
    "unsupported_protocol_version",
    "invalid_message",
    "unknown_runner",
    "not_registered",
]


class Envelope(VersionedPayload):
    schema_version: Literal[1] = 1
    message_id: UUID
    correlation_id: str = Field(max_length=128)  # "run:<uuid>", "req:<uuid>" or a trace id
    sent_at: datetime


class _Part(BaseModel):
    """A part of a message: frozen, no unknown fields, no version of its own."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class SchemaRef(_Part):
    """Names a @versioned model, e.g. ("enrichment", "result", 1)."""

    family: str = Field(max_length=32)
    name: str = Field(max_length=64)
    version: int = Field(ge=1)


class McpServerInfo(_Part):
    """One MCP server of a profile (P2-10): its name, transport and a redacted target (the
    command's name, or the URL's host); never its arguments, env or headers (FR-5.12)."""

    name: str = Field(max_length=128)
    transport: Literal["stdio", "http"]
    target: str | None = Field(default=None, max_length=253)


class ProfileInfo(_Part):
    name: str = Field(pattern=NAME_RE)
    distribution_name: str | None = Field(default=None, max_length=128)
    distribution_version: str | None = Field(default=None, max_length=64)


# --- daemon -> server ------------------------------------------------------------------


@versioned("runner", "register", 1)
class Register(Envelope):
    type: Literal["register"] = "register"
    protocol_versions: list[int] = Field(min_length=1, max_length=16)  # e.g. [1]
    runner_name: str = Field(pattern=NAME_RE)
    host: str = Field(max_length=253)
    os: Literal["linux", "darwin"]
    daemon_version: str = Field(max_length=64)  # semver
    hermes_version: str | None = Field(max_length=64)
    profiles: list[ProfileInfo] = Field(max_length=500)
    capabilities: list[Literal["run", "provision", "health"]]
    running_run_ids: list[UUID] = Field(default=[], max_length=500)  # alive after a reconnect


@versioned("runner", "heartbeat", 1)
class Heartbeat(Envelope):
    type: Literal["heartbeat"] = "heartbeat"
    seq: int = Field(ge=0)
    running_run_ids: list[UUID] = Field(default=[], max_length=500)


@versioned("runner", "result", 1)
class Result(Envelope):
    type: Literal["result"] = "result"
    run_id: UUID
    status: Literal["succeeded", "failed", "timed_out"]  # a subset of RunStatus
    exit_code: int | None
    output_json: dict[str, Any] | None  # the one JSON object the skill replied with
    text: str = Field(max_length=TEXT_MAX)  # final text, for the run log
    error: str | None = Field(default=None, max_length=ERROR_MAX)
    duration_ms: int = Field(ge=0)
    tokens: dict[str, int] | None = None  # from the stream-json result record
    hermes_session_id: str | None = Field(default=None, max_length=256)


@versioned("runner", "health_report", 1)
class HealthReport(Envelope):
    type: Literal["health_report"] = "health_report"
    request_id: UUID
    profile: str = Field(pattern=NAME_RE)
    profile_exists: bool
    reachable: bool
    authenticated: bool | None
    hermes_version: str | None = Field(max_length=64)
    mcp_servers: list[str] = Field(default=[], max_length=200)  # names only (FR-5.12)
    error: str | None = Field(default=None, max_length=ERROR_MAX)
    # P2-10; defaults keep a report without them valid
    mcp_server_details: list[McpServerInfo] = Field(default=[], max_length=200)
    profile_version: str | None = Field(default=None, max_length=64)  # profiles/*/VERSION
    github: TokenReach | None = None
    coolify: TokenReach | None = None


@versioned("runner", "ack", 1)
class Ack(Envelope):
    """Protocol 1: one ack per message, in either direction."""

    type: Literal["ack"] = "ack"
    ack_of: UUID


@versioned("runner", "provision_result", 1)
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


@versioned("runner", "registered", 1)
class Registered(Envelope):
    type: Literal["registered"] = "registered"
    runner_id: UUID
    protocol_version: int  # highest version in both lists
    heartbeat_interval_s: Literal[15] = 15  # architecture: every 15 seconds


@versioned("runner", "run", 1)
class Run(Envelope):
    type: Literal["run"] = "run"
    run_id: UUID
    profile: str = Field(pattern=NAME_RE)
    skill: str = Field(pattern=SKILL_RE)
    packet: dict[str, Any]  # the whole TaskPacket, including prompt_text
    output_schema: SchemaRef
    timeout_s: int = Field(ge=10, le=3600)
    workdir_policy: Literal["none", "worktree"] = "none"  # protocol 1 sends only "none"


@versioned("runner", "health_check", 1)
class HealthCheck(Envelope):
    type: Literal["health_check"] = "health_check"
    request_id: UUID
    profile: str = Field(pattern=NAME_RE)
    # P2-10: what the daemon's token reach probes look at; defaults keep P1-04's valid
    own_repos: list[str] = Field(default=[], max_length=MAX_REACH_TARGETS)  # "owner/name"
    foreign_repos: list[str] = Field(default=[], max_length=MAX_REACH_TARGETS)
    own_apps: list[str] = Field(default=[], max_length=MAX_REACH_TARGETS)  # Coolify UUIDs
    foreign_apps: list[str] = Field(default=[], max_length=MAX_REACH_TARGETS)
    # compared with the profile's own COOLIFY_BASE_URL; never a token destination (decision 18)
    coolify_base_url: str | None = Field(default=None, max_length=2048)


@versioned("runner", "provision", 1)
class Provision(Envelope):
    """Schema reserved here; behavior in P1-06."""

    type: Literal["provision"] = "provision"
    request_id: UUID
    profile: str = Field(pattern=NAME_RE)
    mode: Literal["create", "link"]
    template: Literal["project-template"]
    template_version: str = Field(max_length=64)


@versioned("runner", "error", 1)
class ProtocolError(Envelope):
    type: Literal["error"] = "error"
    code: ErrorCode
    detail: str = Field(max_length=ERROR_MAX)


DaemonMessage = Annotated[
    Register | Heartbeat | Result | HealthReport | ProvisionResult | Ack,
    Field(discriminator="type"),
]
ServerMessage = Annotated[
    Registered | Run | HealthCheck | Provision | Ack | ProtocolError,
    Field(discriminator="type"),
]

_DAEMON: Final[TypeAdapter[DaemonMessage]] = TypeAdapter(DaemonMessage)
_SERVER: Final[TypeAdapter[ServerMessage]] = TypeAdapter(ServerMessage)


class InvalidMessage(ValueError):  # noqa: N818  # the protocol's own word
    """A frame that is not a valid message; `code` is the `error` message's code."""

    def __init__(self, code: ErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.code: ErrorCode = code
        self.detail = detail


def _parse[T](adapter: TypeAdapter[T], raw: str | bytes) -> T:
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise InvalidMessage("invalid_message", "frame is not JSON") from exc
    if not isinstance(data, dict):
        raise InvalidMessage("invalid_message", "frame is not a JSON object")
    version = data.get("schema_version")
    if version != 1 or isinstance(version, bool):
        raise InvalidMessage(
            "unsupported_schema_version", f"schema_version {version!r} is not supported"
        )
    try:
        return adapter.validate_python(data)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise InvalidMessage("invalid_message", f"invalid fields: {', '.join(fields)}") from exc


def parse_daemon(raw: str | bytes) -> DaemonMessage:
    """A daemon frame as its message; InvalidMessage (`unsupported_schema_version` for a
    version other than 1, else `invalid_message`) when it is not one."""
    return _parse(_DAEMON, raw)


def parse_server(raw: str | bytes) -> ServerMessage:
    """A server frame as its message; InvalidMessage when it is not one."""
    return _parse(_SERVER, raw)


def negotiate(versions: Iterable[int]) -> int | None:
    """The highest protocol version both sides speak, None when they share none."""
    shared = set(versions) & set(SERVER_PROTOCOL_VERSIONS)
    return max(shared) if shared else None
