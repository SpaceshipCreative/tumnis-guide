"""agents pure rules: no I/O, `now` passed in (P1-04, FR-5.9, R-22).

- The run vocabulary (R-22): `RunKind` and `RunStatus` hold every value phases 1 to 4 use,
  so later phases change behavior and never the `runs` CHECK constraints.
- Runner presence: a runner heartbeats every `HEARTBEAT_S` seconds; it is offline once
  `MISSED_BEATS` beats are missed (strictly more than 45 s since the last one), and
  `never_seen` until its first register.
- Profile and runner names: `NAME_RE` (also what the runner protocol accepts), so a name
  can never carry a path or a shell trick; a few names are reserved.
- Project profiles (P1-06): `profile_name_for` names a project's Hermes profile after the
  project, and `provision_outcome` turns the runner's answer into the profile's status.
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
import unicodedata
from collections.abc import Iterable, Sequence, Set
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Final, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

NAME_RE: Final = r"^[a-z0-9][a-z0-9-]{0,62}$"  # profile and runner names; blocks shell tricks
SKILL_RE: Final = r"^[a-z][a-z0-9-]{0,40}$"
HEARTBEAT_S: Final = 15  # architecture: every 15 seconds
MISSED_BEATS: Final = 3
RESERVED_PROFILE_NAMES: Final = frozenset({"default", "root", "hermes", "tumnis"})
MASTER_PROFILE_NAME: Final = "tumnis-master"  # the master's profile; never a project's
PROFILE_NAME_MAX: Final = 40  # a project profile's generated name (plan default)
FALLBACK_PROFILE_NAME: Final = "project"
MAX_REACH_TARGETS: Final = 50  # repos or apps probed per kind (plan default)

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


def profile_name_for(project_name: str, taken: Set[str]) -> str:
    """The Hermes profile name for a project: lower-case and ASCII-folded, runs of anything
    but [a-z0-9] as one dash, no dash at either end, at most PROFILE_NAME_MAX characters;
    'project' when nothing is left. A name in `taken`, reserved, or the master's gets
    '-2', '-3' … (cut so the suffix fits)."""
    folded = unicodedata.normalize("NFKD", project_name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    base = slug[:PROFILE_NAME_MAX].rstrip("-") or FALLBACK_PROFILE_NAME
    unavailable = set(taken) | RESERVED_PROFILE_NAMES | {MASTER_PROFILE_NAME}
    if base not in unavailable:
        return base
    n = 2
    while True:
        suffix = f"-{n}"
        candidate = base[: PROFILE_NAME_MAX - len(suffix)].rstrip("-") + suffix
        if candidate not in unavailable:
            return candidate
        n += 1


class ProvisionAnswer(Protocol):
    """What `provision_outcome` reads of the runner's `provision_result`."""

    @property
    def status(self) -> str: ...


ProvisionOutcome = Literal["ready", "not_provisioned"]


def provision_outcome(
    result: ProvisionAnswer | None, *, mode: Literal["create", "link"]
) -> ProvisionOutcome:
    """created or exists (create) and linked (link) -> ready; failed, an answer that does
    not fit the mode, or no answer in time (None) -> not_provisioned."""
    if result is None:
        return "not_provisioned"
    ok = {"create": {"created", "exists"}, "link": {"linked"}}[mode]
    return "ready" if result.status in ok else "not_provisioned"


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


# --- Tool allowlists and token reach (P2-10, SAF-2, SAF-3) -------------------------------


@dataclass(frozen=True)
class Drift:
    extra: frozenset[str]  # present in the profile, not allowed: degraded
    missing: frozenset[str]  # allowed, not present: warning only


def allowlist_drift(reported: Iterable[str], allowlist: Iterable[str]) -> Drift:
    """The servers a profile has beyond its project's allowlist (`extra`), and the allowed
    ones it lacks (`missing`); order and repetition do not matter."""
    have, allowed = frozenset(reported), frozenset(allowlist)
    return Drift(extra=have - allowed, missing=allowed - have)


class TokenReach(BaseModel):
    """What one token reaches, as the daemon probed it on the host (the token itself never
    leaves the host). GitHub: a repo is reachable only with `permissions.push` or `admin`;
    Coolify: an application is reachable when the token may read it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    token_present: bool
    own_reachable: dict[str, bool] = Field(default={}, max_length=MAX_REACH_TARGETS)
    foreign_reachable: list[str] = Field(default=[], max_length=MAX_REACH_TARGETS)
    errors: list[Annotated[str, Field(max_length=300)]] = Field(default=[], max_length=100)


ReachLevel = Literal["ok", "warning", "degraded"]
HealthStatus = Literal["ok", "warning", "degraded", "offline"]


@dataclass(frozen=True)
class ReachVerdict:
    level: ReachLevel
    foreign: tuple[str, ...] = ()  # "github:owner/repo", "coolify:<app uuid>"
    reasons: tuple[str, ...] = ()  # why a warning, in words


def reach_verdict(github: TokenReach | None, coolify: TokenReach | None) -> ReachVerdict:
    """degraded if any foreign_reachable; warning if a token is missing or cannot reach its
    own repo or app (or a probe failed); ok otherwise. A kind the daemon did not probe
    (None: nothing linked) says nothing."""
    foreign: list[str] = []
    reasons: list[str] = []
    for kind, reach in (("github", github), ("coolify", coolify)):
        if reach is None:
            continue
        foreign += [f"{kind}:{target}" for target in reach.foreign_reachable]
        if not reach.token_present:
            reasons.append(f"{kind}: no token in the profile")
            continue
        reasons += [
            f"{kind}: cannot reach its own {target}"
            for target, ok in sorted(reach.own_reachable.items())
            if not ok
        ]
        reasons += [f"{kind}: {error}" for error in reach.errors]
    level: ReachLevel = "degraded" if foreign else "warning" if reasons else "ok"
    return ReachVerdict(level, tuple(foreign), tuple(reasons))


def profile_health(
    reachable: bool, authenticated: bool | None, drift: Drift, reach: ReachVerdict
) -> HealthStatus:
    """offline when the runner cannot reach the profile; degraded on a server outside the
    allowlist or a token reaching another project; warning when not signed in, an allowed
    server is missing, or a token falls short; ok otherwise. A degraded profile still runs
    tasks: the human decides."""
    if not reachable:
        return "offline"
    if drift.extra or reach.level == "degraded":
        return "degraded"
    if authenticated is False or drift.missing or reach.level == "warning":
        return "warning"
    return "ok"


_GITHUB_REPO: Final = re.compile(
    r"^(?:(?:https?://|ssh://git@)github\.com/|git@github\.com:)?"
    r"([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"
)


def github_repo(value: str) -> str | None:
    """`owner/name` (lower case) of a GitHub repo link: `owner/name`, a github.com https
    or ssh URL; None for anything else (another host, a path)."""
    found = _GITHUB_REPO.fullmatch(value.strip())
    if found is None or found.group(2) in {".", ".."}:
        return None
    return f"{found.group(1)}/{found.group(2)}".lower()


@dataclass(frozen=True)
class ReachTargets:
    """What the health check asks the daemon to probe for one project's profile."""

    own_repos: tuple[str, ...] = ()
    foreign_repos: tuple[str, ...] = ()
    own_apps: tuple[str, ...] = ()
    foreign_apps: tuple[str, ...] = ()
    owners: tuple[tuple[str, UUID], ...] = ()  # (target, project) for naming foreign reach


def reach_targets(
    project_id: UUID | None,
    repos: Sequence[tuple[UUID, str]],
    apps: Sequence[tuple[UUID, str]],
    *,
    limit: int = MAX_REACH_TARGETS,
) -> ReachTargets:
    """Own and foreign repos and apps for a profile of `project_id` (None: the master,
    which owns none), from every project's (project, repo link or URL) and (project, app
    uuid); each list deduplicated in order and cut at `limit` (plan default 50). A target
    two projects share counts as own."""

    def split(pairs: Iterable[tuple[UUID, str]]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        own = list(dict.fromkeys(t for p, t in pairs if p == project_id))
        foreign = list(dict.fromkeys(t for p, t in pairs if p != project_id and t not in own))
        return tuple(own[:limit]), tuple(foreign[:limit])

    repo_pairs = [(p, r) for p, value in repos if (r := github_repo(value)) is not None]
    app_pairs = [(p, a.strip()) for p, a in apps if a.strip()]
    own_repos, foreign_repos = split(repo_pairs)
    own_apps, foreign_apps = split(app_pairs)
    owners = tuple(
        dict.fromkeys(
            [(f"github:{t}", p) for p, t in repo_pairs if p != project_id]
            + [(f"coolify:{t}", p) for p, t in app_pairs if p != project_id]
        )
    )
    return ReachTargets(own_repos, foreign_repos, own_apps, foreign_apps, owners)


# --- Enrichment (P1-08, FR-4.4): red-phase seams; the spec tests turn them green ---------


class TaskSnapshot(BaseModel):
    """The task as the enrichment reads it (P1-08)."""

    model_config = ConfigDict(frozen=True)

    def __init__(self, **data: object) -> None:
        raise NotImplementedError("P1-08")


def missing_fields(t: TaskSnapshot) -> list[str]:
    raise NotImplementedError("P1-08")


def needs_enrichment(t: TaskSnapshot) -> bool:
    raise NotImplementedError("P1-08")


def merge_enrichment(
    current: TaskSnapshot,
    res: object,
    *,
    requested: Sequence[str],
    estimate_range: tuple[int, int],
) -> Any:
    raise NotImplementedError("P1-08")


def plausibility_flag(answer: object | None, route: str) -> str | None:
    raise NotImplementedError("P1-08")
