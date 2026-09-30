"""agents pure rules: no I/O, `now` passed in (P1-04, FR-5.9, R-22).

- The run vocabulary (R-22): `RunKind` and `RunStatus` hold every value phases 1 to 4 use,
  so later phases change behavior and never the `runs` CHECK constraints.
- Runner presence: a runner heartbeats every `HEARTBEAT_S` seconds; it is offline once
  `MISSED_BEATS` beats are missed (strictly more than 45 s since the last one), and
  `never_seen` until its first register.
- Profile and runner names: `NAME_RE` (also what the runner protocol accepts), so a name
  can never carry a path or a shell trick; a few names are reserved.
- `select_runner`: the runner a daemon-transport profile runs on, when it is online and
  lists the profile in its inventory.
- Artifacts (P2-07): `artifact_refusal`, the `nack` code for an `upload_artifact` the
  server will not store (small UTF-8 text of a few media types only); the sha256 is
  computed by the caller, since hashing is not a rule.
- Skill replies (P1-05): `enrichment_errors` and `planning_errors`, the cross-field checks
  a JSON Schema cannot express, shared by the skill harness and the workflows that apply
  a reply (P1-08, P1-11). They read the skill models of `skill_io.py` structurally.
"""

import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

NAME_RE: Final = r"^[a-z0-9][a-z0-9-]{0,62}$"  # profile and runner names; blocks shell tricks
SKILL_RE: Final = r"^[a-z][a-z0-9-]{0,40}$"
HEARTBEAT_S: Final = 15  # architecture: every 15 seconds
MISSED_BEATS: Final = 3
RESERVED_PROFILE_NAMES: Final = frozenset({"default", "root", "hermes", "tumnis"})

Label = Literal["human", "ai", "hybrid"]  # the task labels (FR-4.1)
ESTIMATED_LABELS: Final[frozenset[str]] = frozenset({"human", "hybrid"})

_NAME: Final = re.compile(NAME_RE)


class RunKind(StrEnum):
    ENRICH = "enrich"
    PLAN = "plan"
    TASK = "task"
    PROPOSAL = "proposal"
    STUCK = "stuck"
    NOTIFY = "notify"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_ON_HUMAN = "waiting_on_human"
    HELD = "held"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    RUNNER_LOST = "runner_lost"


TERMINAL_STATUSES: Final = frozenset(
    {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.TIMED_OUT,
        RunStatus.RUNNER_LOST,
    }
)

RunnerStatus = Literal["online", "offline", "never_seen"]


class InvalidProfileName(ValueError):  # noqa: N818  # carries the problem code
    """A profile name outside NAME_RE, or a reserved one (422 `invalid_profile_name`)."""

    code = "invalid_profile_name"


class RunnerDTO(BaseModel):
    """What the rules need of a runner row."""

    id: UUID
    name: str
    last_heartbeat_at: datetime | None
    inventory: list[str] = []  # profile names from its last register


class AgentProfileDTO(BaseModel):
    """What the rules need of an agent profile row."""

    id: UUID
    name: str
    role: Literal["master", "project"]
    transport: Literal["daemon", "mcp_endpoint"]
    runner_id: UUID | None = None
    status: str = "registered"


def runner_status(last_heartbeat_at: datetime | None, now: datetime) -> RunnerStatus:
    """offline when now - last_heartbeat_at > MISSED_BEATS * HEARTBEAT_S (strictly
    greater); never_seen without any heartbeat."""
    if last_heartbeat_at is None:
        return "never_seen"
    if now - last_heartbeat_at > timedelta(seconds=MISSED_BEATS * HEARTBEAT_S):
        return "offline"
    return "online"


def validate_profile_name(name: str) -> str:
    """The name when it matches NAME_RE and is not reserved; InvalidProfileName otherwise."""
    if not _NAME.fullmatch(name):
        raise InvalidProfileName(
            "A profile name is 1 to 63 of a-z, 0-9 and '-', starting with a letter or digit"
        )
    if name in RESERVED_PROFILE_NAMES:
        raise InvalidProfileName(f"{name!r} is a reserved name")
    return name


def select_runner(
    profile: AgentProfileDTO, runners: Sequence[RunnerDTO], now: datetime
) -> RunnerDTO | None:
    """The profile's runner when it is online and its last register listed the profile;
    None otherwise (and always for an mcp_endpoint profile, which needs no runner)."""
    if profile.transport != "daemon" or profile.runner_id is None:
        return None
    for runner in runners:
        if runner.id != profile.runner_id:
            continue
        online = runner_status(runner.last_heartbeat_at, now) == "online"
        return runner if online and profile.name in runner.inventory else None
    return None


# --- artifacts (P2-07) --------------------------------------------------------------------

ARTIFACT_MAX_BYTES: Final = 256 * 1024  # plan default
ARTIFACT_MEDIA_TYPES: Final = frozenset(
    {"text/plain", "text/markdown", "text/x-diff", "application/json"}
)
ArtifactRefusal = Literal["too_large", "bad_media_type", "not_utf8", "sha_mismatch"]


def artifact_bytes(content: str) -> bytes | None:
    """The content as UTF-8, None when it cannot be (a lone surrogate from a JSON escape)."""
    try:
        return content.encode("utf-8")
    except UnicodeEncodeError:
        return None


def artifact_refusal(
    media_type: str, content: str, size: int, sha256: str, computed_sha256: str | None
) -> ArtifactRefusal | None:
    """Why the server refuses an artifact, checked in this order; None to store it.

    - media type not text/plain, text/markdown, text/x-diff or application/json
                                                                  -> 'bad_media_type'
    - content not UTF-8 text (a NUL byte, or a lone surrogate)    -> 'not_utf8'
    - more than ARTIFACT_MAX_BYTES of UTF-8, declared or actual   -> 'too_large'
    - declared size or sha256 not those of the content            -> 'sha_mismatch'
    """
    if media_type not in ARTIFACT_MEDIA_TYPES:
        return "bad_media_type"
    raw = artifact_bytes(content)
    if raw is None or "\x00" in content:
        return "not_utf8"
    if len(raw) > ARTIFACT_MAX_BYTES or size > ARTIFACT_MAX_BYTES:
        return "too_large"
    if size != len(raw) or computed_sha256 is None or sha256.lower() != computed_sha256:
        return "sha_mismatch"
    return None


# --- skill replies (P1-05) ---------------------------------------------------------------


class _EnrichTaskView(Protocol):
    @property
    def id(self) -> UUID: ...
    @property
    def label(self) -> Label: ...


class EnrichmentRequestView(Protocol):
    """What the rule reads of an `EnrichmentRequest` (skill_io.py)."""

    @property
    def task(self) -> _EnrichTaskView: ...


class _LabelRevisionView(Protocol):
    @property
    def label(self) -> Label: ...


class EnrichmentResultView(Protocol):
    """What the rule reads of an `EnrichmentResult` (skill_io.py)."""

    @property
    def task_id(self) -> UUID: ...
    @property
    def estimate_minutes(self) -> int | None: ...
    @property
    def label_revision(self) -> _LabelRevisionView | None: ...
    @property
    def hybrid_split(self) -> object | None: ...


def enrichment_errors(req: EnrichmentRequestView, res: EnrichmentResultView) -> list[str]:
    """The cross-field rules an enrichment result breaks, in a fixed order; [] when none.

    effective = res.label_revision.label if present else req.task.label
    - res.task_id == req.task.id                                   -> 'task_id_mismatch'
    - estimate_minutes present iff effective in {human, hybrid}   -> 'estimate_for_ai',
                                                                      'estimate_missing'
    - hybrid_split present iff effective == hybrid                -> 'hybrid_split_missing',
                                                                      'hybrid_split_unexpected'
    """
    effective = res.label_revision.label if res.label_revision is not None else req.task.label
    errors: list[str] = []
    if res.task_id != req.task.id:
        errors.append("task_id_mismatch")
    estimated = effective in ESTIMATED_LABELS
    if res.estimate_minutes is not None and not estimated:
        errors.append("estimate_for_ai")
    elif res.estimate_minutes is None and estimated:
        errors.append("estimate_missing")
    hybrid = effective == "hybrid"
    if res.hybrid_split is None and hybrid:
        errors.append("hybrid_split_missing")
    elif res.hybrid_split is not None and not hybrid:
        errors.append("hybrid_split_unexpected")
    return errors


class _CandidateView(Protocol):
    @property
    def task_id(self) -> UUID: ...


class PlanningRequestView(Protocol):
    """What the rule reads of a `PlanningRequest` (skill_io.py)."""

    @property
    def max_items(self) -> int: ...
    @property
    def candidates(self) -> Sequence[_CandidateView]: ...


class _PickView(Protocol):
    @property
    def task_id(self) -> UUID: ...


class PlanningResultView(Protocol):
    """What the rule reads of a `PlanningResult` (skill_io.py)."""

    @property
    def picks(self) -> Sequence[_PickView]: ...
    @property
    def alternates(self) -> Sequence[UUID]: ...


def planning_errors(req: PlanningRequestView, res: PlanningResultView) -> list[str]:
    """The rules a planning result breaks, in a fixed order; [] when none: every pick and
    alternate is one of the request's candidates (no invented task ids), no task is picked
    twice, and there are at most `max_items` picks."""
    offered = {c.task_id for c in req.candidates}
    picked = [p.task_id for p in res.picks]
    errors: list[str] = []
    if any(task_id not in offered for task_id in picked):
        errors.append("unknown_pick")
    if len(set(picked)) != len(picked):
        errors.append("duplicate_pick")
    if len(picked) > req.max_items:
        errors.append("too_many_picks")
    if any(task_id not in offered for task_id in res.alternates):
        errors.append("unknown_alternate")
    return errors
