"""Runner protocols 1 and 2 (P1-04, P2-07, R-25): the daemon's own copies of the messages.

The server's models live in `backend/tumnis/modules/agents/protocol.py`; the daemon never
imports the backend. Its contract tests validate every message built here, and every server
message parsed here, against the committed JSON Schemas in `schemas/runner/v<n>/`, so the
two copies cannot drift. All builders live in this module.

Protocol 2 (P2-07) adds `stream`, `status`, `upload_artifact` (daemon to server), `cancel`,
`archive`, `nack` (server to daemon), batched acks (`ack` version 2) and version-2 `run`
and `result`. The server picks protocol 2 only for a daemon that lists 2 and advertises
`stream`, `cancel` and `upload_artifact`; otherwise the session stays on protocol 1.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Any, Final, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

NAME_RE: Final = r"^[a-z0-9][a-z0-9-]{0,62}$"  # profile and runner names
SKILL_RE: Final = r"^[a-z][a-z0-9-]{0,40}$"
PROTOCOL_VERSIONS: Final = (1, 2)  # what this daemon speaks
PROTOCOL_2: Final = 2
# Advertised on register; "archive" joins with P2-18's behavior.
CAPABILITIES: Final = ("run", "health", "stream", "cancel", "upload_artifact", "worktree")
TEXT_MAX: Final = 65_536
ERROR_MAX: Final = 4_096
STREAM_TEXT_MAX: Final = 8_192  # one stream line (plan default, 8 KiB)
TIMEOUT_V2_MAX: Final = 86_400  # R-29
MAX_REACH_TARGETS: Final = 50  # repos or apps probed per kind (plan default)

Capability = Literal[
    "run", "provision", "health", "stream", "cancel", "upload_artifact", "worktree", "archive"
]
StreamKind = Literal["log", "tool_call", "file_touched"]
StatusState = Literal["started", "waiting", "cancelling", "gap"]


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


# --- code location (the packet's body.project.code_location, P2-02's place) -------------


class PathLocation(_Model):
    kind: Literal["path"] = "path"
    path: str = Field(min_length=1, max_length=4096)


class RepoLocation(_Model):
    kind: Literal["repo"] = "repo"
    clone_url: str = Field(min_length=1, max_length=2048)
    default_branch: str | None = Field(default=None, max_length=255)


CodeLocation = PathLocation | RepoLocation


def code_location_of(packet: dict[str, Any]) -> CodeLocation | None:
    """The run's code location from its packet (`body.project.code_location`), None when
    the packet names none. ValueError for a location that is not one."""
    body = packet.get("body")
    project = body.get("project") if isinstance(body, dict) else None
    raw = project.get("code_location") if isinstance(project, dict) else None
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("code_location is not an object")
    try:
        if raw.get("kind") == "path":
            return PathLocation.model_validate(raw)
        if raw.get("kind") == "repo":
            return RepoLocation.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid code_location: {exc.error_count()} errors") from None
    raise ValueError("code_location kind is neither path nor repo")


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
    capabilities: list[Capability]
    running_run_ids: list[UUID] = Field(default=[], max_length=500)


class Heartbeat(Envelope):
    type: Literal["heartbeat"] = "heartbeat"
    seq: int = Field(ge=0)
    running_run_ids: list[UUID] = Field(default=[], max_length=500)
    # Protocol 2 only; left out of the frame when None (a protocol-1 server refuses it).
    outbox_depth: int | None = Field(default=None, ge=0, exclude_if=lambda v: v is None)


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


class ResultV2(Result):
    """Protocol 2's result: status adds `cancelled`."""

    schema_version: Literal[2] = 2  # type: ignore[assignment]
    status: Literal["succeeded", "failed", "timed_out", "cancelled"]  # type: ignore[assignment]


class McpServerInfo(_Model):
    """One MCP server of a profile: its name, transport and a redacted target (the
    command's name, or the URL's host); never its arguments, env or headers (P2-10)."""

    name: str = Field(max_length=128)
    transport: Literal["stdio", "http"]
    target: str | None = Field(default=None, max_length=253)


class TokenReach(_Model):
    """What one token reaches, probed from the host; the token never reaches Tumnis (P2-10)."""

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


class AckBatch(Envelope):
    """Protocol 2: one ack for up to 50 messages, in either direction."""

    schema_version: Literal[2] = 2  # type: ignore[assignment]
    type: Literal["ack"] = "ack"
    message_ids: list[UUID] = Field(min_length=1, max_length=50)


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


class Stream(Envelope):
    type: Literal["stream"] = "stream"
    run_id: UUID
    seq: int = Field(ge=1)
    kind: StreamKind
    text: str = Field(max_length=STREAM_TEXT_MAX)
    ts: datetime


class Status(Envelope):
    type: Literal["status"] = "status"
    run_id: UUID | None
    profile: str | None = Field(pattern=NAME_RE)
    profile_version: str | None = Field(max_length=64)
    state: StatusState
    detail: str | None = Field(default=None, max_length=ERROR_MAX)


class UploadArtifact(Envelope):
    type: Literal["upload_artifact"] = "upload_artifact"
    run_id: UUID
    name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(max_length=255)
    size: int = Field(ge=0)
    sha256: str = Field(max_length=64)
    content: str


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


class RunV2(Run):
    schema_version: Literal[2] = 2  # type: ignore[assignment]
    timeout_s: int = Field(ge=10, le=TIMEOUT_V2_MAX)


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


class Cancel(Envelope):
    type: Literal["cancel"] = "cancel"
    run_id: UUID
    reason: str = Field(max_length=ERROR_MAX)


class Archive(Envelope):
    """Schema only; the behavior arrives with P2-18."""

    type: Literal["archive"] = "archive"
    profile: str = Field(pattern=NAME_RE)
    archive_id: str = Field(max_length=128)


class Nack(Envelope):
    type: Literal["nack"] = "nack"
    nack_of: UUID
    code: Literal["too_large", "bad_media_type", "not_utf8", "sha_mismatch", "unknown_run"]
    detail: str | None = Field(default=None, max_length=ERROR_MAX)


DaemonMessage = (
    Register
    | Heartbeat
    | Result
    | HealthReport
    | ProvisionResult
    | Ack
    | AckBatch
    | Stream
    | Status
    | UploadArtifact
)
ServerMessage = (
    Registered
    | Run
    | HealthCheck
    | Provision
    | Ack
    | AckBatch
    | ProtocolError
    | Cancel
    | Archive
    | Nack
)
_SERVER: Final[dict[tuple[str, int], type[_Model]]] = {
    ("registered", 1): Registered,
    ("run", 1): Run,
    ("run", 2): RunV2,
    ("health_check", 1): HealthCheck,
    ("provision", 1): Provision,
    ("ack", 1): Ack,
    ("ack", 2): AckBatch,
    ("error", 1): ProtocolError,
    ("cancel", 1): Cancel,
    ("archive", 1): Archive,
    ("nack", 1): Nack,
}
# Protocol-2 daemon messages: never sent, nor kept, on a protocol-1 session.
PROTOCOL_2_ONLY: Final = frozenset({"stream", "status", "upload_artifact"})


class InvalidFrame(ValueError):  # noqa: N818  # the protocol's word
    """A server frame that is not a message this daemon reads."""


def parse_server(raw: str | bytes) -> ServerMessage:
    """A server frame as its message, chosen by (`type`, `schema_version`)."""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise InvalidFrame("frame is not JSON") from exc
    if not isinstance(data, dict):
        raise InvalidFrame("frame is not a JSON object")
    version = data.get("schema_version")
    model = None
    if isinstance(version, int) and not isinstance(version, bool):
        model = _SERVER.get((str(data.get("type")), version))
    if model is None:
        raise InvalidFrame("unsupported message type or schema_version")
    try:
        message: ServerMessage = model.model_validate(data)  # type: ignore[assignment]
    except ValidationError as exc:
        raise InvalidFrame(f"invalid message: {exc.error_count()} errors") from exc
    return message


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
        capabilities=list(CAPABILITIES),
        running_run_ids=running_run_ids,
    )


def make_heartbeat(
    runner_name: str, seq: int, running_run_ids: list[UUID], outbox_depth: int | None = None
) -> Heartbeat:
    return Heartbeat(
        **envelope(f"runner:{runner_name}"),
        seq=seq,
        running_run_ids=running_run_ids,
        outbox_depth=outbox_depth,
    )


def make_ack(message: Envelope) -> Ack:
    return Ack(**envelope(message.correlation_id), ack_of=message.message_id)


def make_ack_batch(correlation_id: str, message_ids: list[UUID]) -> AckBatch:
    return AckBatch(**envelope(correlation_id), message_ids=message_ids)


def make_stream(run_id: UUID, correlation_id: str, seq: int, kind: StreamKind, text: str) -> Stream:
    """One stream line; text past 8 KiB is cut (the line's start is what the view shows)."""
    return Stream(
        **envelope(correlation_id),
        run_id=run_id,
        seq=seq,
        kind=kind,
        text=text[:STREAM_TEXT_MAX],
        ts=now(),
    )


def make_status(
    run_id: UUID | None,
    correlation_id: str,
    state: StatusState,
    *,
    profile: str | None = None,
    profile_version: str | None = None,
    detail: str | None = None,
) -> Status:
    return Status(
        **envelope(correlation_id),
        run_id=run_id,
        profile=profile,
        profile_version=profile_version,
        state=state,
        detail=detail[:ERROR_MAX] if detail is not None else None,
    )


def make_artifact(
    run_id: UUID, correlation_id: str, name: str, media_type: str, content: str
) -> UploadArtifact:
    """A text artifact with the size and sha256 of its UTF-8 bytes (the server checks
    both, and refuses anything but small UTF-8 text)."""
    raw = content.encode("utf-8")
    return UploadArtifact(
        **envelope(correlation_id),
        run_id=run_id,
        name=name,
        media_type=media_type,
        size=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        content=content,
    )
