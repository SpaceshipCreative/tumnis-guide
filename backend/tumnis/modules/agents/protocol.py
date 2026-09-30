"""Runner protocols 1 and 2 (P1-04, P2-07, R-25, FR-5.11): the messages the runner daemon
and the api exchange over `/ws/runner`, one JSON text frame each.

Every message is a `VersionedPayload` with an integer `schema_version` (R-02), registered
as `@versioned("runner", "<type>", 1)`: `make gen` writes `schemas/runner/v1/<type>.json`,
which the daemon's own copies of these models are checked against (its contract test), so
the two sides cannot drift. `register` lists the protocol versions the daemon speaks; the
server answers `registered` with the highest version both support, or
`error{unsupported_protocol_version}` and a close. Protocol 1 acknowledges every message,
in either direction, with its own `ack{ack_of}`; protocol 2 (P2-07) adds batched acks
(`ack` version 2, `message_ids`), `stream`, `status`, `cancel`, `upload_artifact`,
`archive` and `nack` (new types at version 1) and version-2 `run` and `result`. A daemon
gets protocol 2 only when it lists 2 and advertises `PROTOCOL_2_CAPABILITIES`, so a
daemon that merely lists the number stays on protocol 1.

A frame is parsed by its (`type`, `schema_version`): a known type at a version this side
does not read is `unsupported_schema_version`.
"""

import json
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tumnis.core.schemas import VersionedPayload, upgrader, versioned
from tumnis.modules.agents.rules import NAME_RE, SKILL_RE

__all__ = [
    "NAME_RE",
    "PROTOCOL_2_CAPABILITIES",
    "SERVER_PROTOCOL_VERSIONS",
    "Ack",
    "AckBatch",
    "Archive",
    "Cancel",
    "DaemonMessage",
    "Envelope",
    "HealthCheck",
    "HealthReport",
    "Heartbeat",
    "InvalidMessage",
    "Nack",
    "ProfileInfo",
    "ProtocolError",
    "Provision",
    "ProvisionResult",
    "Register",
    "Registered",
    "Result",
    "ResultV2",
    "Run",
    "RunV2",
    "SchemaRef",
    "ServerMessage",
    "Status",
    "Stream",
    "UploadArtifact",
    "negotiate",
    "parse_daemon",
    "parse_server",
]

SERVER_PROTOCOL_VERSIONS: Final = (1, 2)
# A daemon gets protocol 2 only when it lists 2 and advertises all of these.
PROTOCOL_2_CAPABILITIES: Final = frozenset({"stream", "cancel", "upload_artifact"})
TEXT_MAX: Final = 65_536
ERROR_MAX: Final = 4_096

STREAM_TEXT_MAX: Final = 8_192  # one stream line (plan default, 8 KiB)
TIMEOUT_V2_MAX: Final = 86_400  # R-29: the 24-hour ceiling; the server cancels earlier

Capability = Literal[
    "run", "provision", "health", "stream", "cancel", "upload_artifact", "worktree", "archive"
]
NackCode = Literal["too_large", "bad_media_type", "not_utf8", "sha_mismatch", "unknown_run"]

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
    capabilities: list[Capability]  # protocol 2 adds stream, cancel, upload_artifact, ...
    running_run_ids: list[UUID] = Field(default=[], max_length=500)  # alive after a reconnect


@versioned("runner", "heartbeat", 1)
class Heartbeat(Envelope):
    type: Literal["heartbeat"] = "heartbeat"
    seq: int = Field(ge=0)
    running_run_ids: list[UUID] = Field(default=[], max_length=500)
    # Protocol 2: unacked messages in the daemon's outbox; left out of the frame when None,
    # so a protocol-1 heartbeat is unchanged.
    outbox_depth: int | None = Field(default=None, ge=0, exclude_if=lambda v: v is None)


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


@versioned("runner", "ack", 1)
class Ack(Envelope):
    """Protocol 1: one ack per message, in either direction."""

    type: Literal["ack"] = "ack"
    ack_of: UUID


@versioned("runner", "ack", 2)
class AckBatch(Envelope):
    """Protocol 2: one ack for up to 50 messages, in either direction."""

    schema_version: Literal[2] = 2  # type: ignore[assignment]
    type: Literal["ack"] = "ack"
    message_ids: list[UUID] = Field(min_length=1, max_length=50)


@upgrader("runner", "ack", 1)
def _ack_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    ack_of = data.pop("ack_of")
    return {**data, "message_ids": [ack_of]}


@versioned("runner", "result", 2)
class ResultV2(Result):
    """Protocol 2's result: status adds `cancelled`."""

    schema_version: Literal[2] = 2  # type: ignore[assignment]
    status: Literal["succeeded", "failed", "timed_out", "cancelled"]  # type: ignore[assignment]


@upgrader("runner", "result", 1)
def _result_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    return data  # every v1 status is a v2 status


@versioned("runner", "stream", 1)
class Stream(Envelope):
    """Protocol 2: one line of a run's output, numbered per run from 1."""

    type: Literal["stream"] = "stream"
    run_id: UUID
    seq: int = Field(ge=1)
    kind: Literal["log", "tool_call", "file_touched"]
    text: str = Field(max_length=STREAM_TEXT_MAX)
    ts: datetime


@versioned("runner", "status", 1)
class Status(Envelope):
    """Protocol 2: a run's state (`started` carries the profile's VERSION; `gap` declares
    stream lines the daemon's full outbox dropped), or a profile-level report without a
    run."""

    type: Literal["status"] = "status"
    run_id: UUID | None
    profile: str | None = Field(pattern=NAME_RE)
    profile_version: str | None = Field(max_length=64)
    state: Literal["started", "waiting", "cancelling", "gap"]
    detail: str | None = Field(default=None, max_length=ERROR_MAX)


@versioned("runner", "upload_artifact", 1)
class UploadArtifact(Envelope):
    """Protocol 2: a small text artifact of a run. The fields are deliberately loose: the
    server checks media type, UTF-8, size and sha256 itself and answers `nack{code}`."""

    type: Literal["upload_artifact"] = "upload_artifact"
    run_id: UUID
    name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(max_length=255)
    size: int = Field(ge=0)
    sha256: str = Field(max_length=64)
    content: str


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


@versioned("runner", "run", 2)
class RunV2(Run):
    """Protocol 2's run: `timeout_s` carries the 24-hour ceiling (R-29); the server
    enforces the active-time cap itself with `cancel`."""

    schema_version: Literal[2] = 2  # type: ignore[assignment]
    timeout_s: int = Field(ge=10, le=TIMEOUT_V2_MAX)


@upgrader("runner", "run", 1)
def _run_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    return data  # v1's limits sit inside v2's


@versioned("runner", "health_check", 1)
class HealthCheck(Envelope):
    type: Literal["health_check"] = "health_check"
    request_id: UUID
    profile: str = Field(pattern=NAME_RE)


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


@versioned("runner", "cancel", 1)
class Cancel(Envelope):
    """Protocol 2: stop a run; the daemon acks, sends `status{cancelling}`, then a
    `result` with status `cancelled`."""

    type: Literal["cancel"] = "cancel"
    run_id: UUID
    reason: str = Field(max_length=ERROR_MAX)


@versioned("runner", "archive", 1)
class Archive(Envelope):
    """Protocol 2: the schema only; the behavior and its companions arrive in P2-18."""

    type: Literal["archive"] = "archive"
    profile: str = Field(pattern=NAME_RE)
    archive_id: str = Field(max_length=128)


@versioned("runner", "nack", 1)
class Nack(Envelope):
    """Protocol 2: the server refuses a message for good; the daemon drops it."""

    type: Literal["nack"] = "nack"
    nack_of: UUID
    code: NackCode
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

_Models = dict[tuple[str, int], type[VersionedPayload]]
_DAEMON: Final[_Models] = {
    ("register", 1): Register,
    ("heartbeat", 1): Heartbeat,
    ("result", 1): Result,
    ("result", 2): ResultV2,
    ("health_report", 1): HealthReport,
    ("provision_result", 1): ProvisionResult,
    ("ack", 1): Ack,
    ("ack", 2): AckBatch,
    ("stream", 1): Stream,
    ("status", 1): Status,
    ("upload_artifact", 1): UploadArtifact,
}
_SERVER: Final[_Models] = {
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


class InvalidMessage(ValueError):  # noqa: N818  # the protocol's own word
    """A frame that is not a valid message; `code` is the `error` message's code."""

    def __init__(self, code: ErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.code: ErrorCode = code
        self.detail = detail


def _parse(models: _Models, raw: str | bytes) -> Any:
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise InvalidMessage("invalid_message", "frame is not JSON") from exc
    if not isinstance(data, dict):
        raise InvalidMessage("invalid_message", "frame is not a JSON object")
    version = data.get("schema_version")
    type_ = data.get("type")
    known = {v for (t, v) in models if t == type_} or {v for (_, v) in models}
    if isinstance(version, bool) or version not in known:
        raise InvalidMessage(
            "unsupported_schema_version", f"schema_version {version!r} is not supported"
        )
    model = models.get((str(type_), version))
    if model is None:
        raise InvalidMessage("invalid_message", "unknown message type")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise InvalidMessage("invalid_message", f"invalid fields: {', '.join(fields)}") from exc


def parse_daemon(raw: str | bytes) -> DaemonMessage:
    """A daemon frame as its message; InvalidMessage (`unsupported_schema_version` for a
    version this side does not read, else `invalid_message`) when it is not one."""
    message: DaemonMessage = _parse(_DAEMON, raw)
    return message


def parse_server(raw: str | bytes) -> ServerMessage:
    """A server frame as its message; InvalidMessage when it is not one."""
    message: ServerMessage = _parse(_SERVER, raw)
    return message


def negotiate(versions: Iterable[int], capabilities: Iterable[str] = ()) -> int | None:
    """The highest protocol version both sides speak, None when they share none. Protocol
    2 counts only for a daemon that advertises `PROTOCOL_2_CAPABILITIES`."""
    shared = set(versions) & set(SERVER_PROTOCOL_VERSIONS)
    if not PROTOCOL_2_CAPABILITIES.issubset(capabilities):
        shared.discard(2)
    return max(shared) if shared else None
