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

import functools
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Final, Literal, Protocol
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


# --- runs (P2-04, SAF-5, FR-5.4, R-22, R-29) ----------------------------------------------

RUN_TRANSITIONS: Final[Mapping[RunStatus, frozenset[RunStatus]]] = {
    RunStatus.QUEUED: frozenset(
        {RunStatus.RUNNING, RunStatus.HELD, RunStatus.CANCELLED, RunStatus.FAILED}
    ),
    RunStatus.HELD: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.WAITING_ON_HUMAN,
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.TIMED_OUT,
            RunStatus.RUNNER_LOST,
        }
    ),
    RunStatus.WAITING_ON_HUMAN: frozenset(
        {
            RunStatus.RUNNING,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.TIMED_OUT,
            RunStatus.RUNNER_LOST,
        }
    ),
}
ACTIVE_RUN_STATUSES: Final = frozenset(RUN_TRANSITIONS)  # queued, held, running, waiting
RUNNABLE_TASK_STATUSES: Final = frozenset({"backlog", "today", "in_progress"})
RUNNABLE_LABELS: Final = frozenset({"ai", "hybrid"})  # kind `task`; `stuck` takes any (R-23)
UNREADY_PROFILE_STATUSES: Final = frozenset({"provisioning", "not_provisioned", "paused"})


class TransitionNotAllowed(Exception):  # noqa: N818  # the plan's name
    """A run status change `RUN_TRANSITIONS` does not allow."""

    code = "run_transition_not_allowed"

    def __init__(self, current: RunStatus, target: RunStatus) -> None:
        super().__init__(f"a run cannot go from {current.value} to {target.value}")
        self.current = current
        self.target = target


def run_transition(current: RunStatus, target: RunStatus) -> RunStatus:
    """`target` when `RUN_TRANSITIONS` allows current -> target; TransitionNotAllowed
    otherwise (a terminal status goes nowhere)."""
    if target not in RUN_TRANSITIONS.get(current, frozenset()):
        raise TransitionNotAllowed(current, target)
    return target


def active_seconds(intervals: Sequence[tuple[datetime, datetime, bool]]) -> float:
    """The summed length of the (start, end, waiting) intervals where waiting is False: time
    spent waiting on a human does not count against the run's active-time cap (SAF-5)."""
    return sum((end - start).total_seconds() for start, end, waiting in intervals if not waiting)


def over_ceiling(started_at: datetime, now: datetime, ceiling: timedelta) -> bool:
    """The run has been going for `ceiling` or longer on the wall clock (R-29), waiting
    included. The ceiling is the caller's (core.limits.RUN_WALL_CLOCK_CEILING by default)."""
    return now - started_at >= ceiling


@dataclass(frozen=True, slots=True)
class DispatchTask:
    """What `can_dispatch` reads of a task: its label (None while pending), its status and
    the kinds of its runs still active."""

    label: str | None
    status: str
    active_kinds: frozenset[RunKind]


@dataclass(frozen=True, slots=True)
class DispatchProfile:
    """What `can_dispatch` reads of the project's agent profile."""

    status: str


RefusalCode = Literal[
    "label_not_runnable", "status_not_runnable", "no_ready_profile", "run_already_active"
]


@dataclass(frozen=True, slots=True)
class Refusal:
    code: RefusalCode
    detail: str


def can_dispatch(
    task: DispatchTask, kind: RunKind, profile: DispatchProfile | None
) -> Refusal | None:
    """None when a run of `kind` may start for the task: kind `task` needs an AI or Hybrid
    label (`stuck` takes any label, R-23); the task is in backlog, today or in progress; the
    project has a ready profile; no run of the same kind is active for the task. The
    Refusal names the first rule that fails."""
    if kind is not RunKind.STUCK and task.label not in RUNNABLE_LABELS:
        return Refusal("label_not_runnable", "Only AI and Hybrid tasks can be run")
    if task.status not in RUNNABLE_TASK_STATUSES:
        return Refusal("status_not_runnable", f"A task in {task.status} cannot be run")
    if profile is None or profile.status in UNREADY_PROFILE_STATUSES:
        return Refusal("no_ready_profile", "The project has no ready agent")
    if kind in task.active_kinds:
        return Refusal("run_already_active", "The task already has an active run of this kind")
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
    def label(self) -> Label | None: ...


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


# --- Untrusted blocks and task tokens (P2-02, SAF-1, R-24, R-27) --------------------------
#
# Every piece of text in a task packet is a `Block`. Trusted text (the user's brief, a human
# comment, a task the user wrote) is rendered plainly; everything else is an untrusted block:
#
#     <untrusted-data id="u-7f3a9c2e5b10d4aa" source="email" item="..." from="...">
#     ...escaped text...
#     </untrusted-data id="u-7f3a9c2e5b10d4aa">
#
# After escaping, the text holds no `<` or `>` at all (nor a character whose compatibility
# form holds one), so it can neither open nor close a tag; the per-packet nonce stops a
# forged closer written into one block from matching another block's id.

# Look-alikes of < and > (Unicode UTS #39 confusables, https://www.unicode.org/reports/tr39/),
# written as numeric entities. A character whose NFKD form holds < or > is escaped too,
# listed here or not (`_folds_to_bracket`).
CONFUSABLE_BRACKETS: Final = frozenset(
    "\uff1c\uff1e"  # fullwidth less-than and greater-than signs
    "\ufe64\ufe65"  # small less-than and greater-than signs
    "\u2039\u203a"  # single angle quotation marks
    "\u27e8\u27e9"  # mathematical angle brackets
    "\u2329\u232a"  # left and right-pointing angle brackets
    "\u3008\u3009"  # CJK angle brackets
    "\u02c2\u02c3"  # modifier letter arrowheads
    "\u1438\u1433"  # Canadian syllabics pa and po
    "\u276e\u276f"  # heavy angle quotation mark ornaments
    "\u226e\u226f"  # not less-than, not greater-than (a bracket and a combining slash)
)
INVISIBLE_CONTROLS: Final = frozenset(
    {chr(c) for c in range(0xE0000, 0xE0080)}  # tag characters ("ASCII smuggling")
    | set("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")  # bidi controls
    | set("\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064\ufeff\u180e")  # zero width
)
NONCE_RE: Final = r"^u-[0-9a-f]{16}$"
ATTR_NAME_RE: Final = r"^[a-z][a-z_-]{0,31}$"
ATTR_LIMIT: Final = 200  # characters of an attribute value kept (plan default)
CONTEXT_ITEM_MAX_BYTES: Final = 32 * 1024  # each context item's text (plan default)
PACKET_MAX_BYTES: Final = 256 * 1024  # a task packet's prompt text (plan default)

_NAMED: Final[dict[str, str]] = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}
_UNNAMED: Final[dict[str, str]] = {"amp": "&", "lt": "<", "gt": ">", "quot": '"'}
_ENTITY: Final = re.compile(r"&(?:(amp|lt|gt|quot)|#x([0-9A-F]{1,6}));")
_NONCE: Final = re.compile(NONCE_RE)
_ATTR_NAME: Final = re.compile(ATTR_NAME_RE)

BlockSource = Literal[
    "user",
    "task",
    "comment",
    "brief",
    "document",
    "email",
    "chat",
    "note",
    "event",
    "artifact",
    "file",
    "url",
    "agent",
]


class Block(BaseModel):
    """One piece of text in a packet: how far it is trusted, whether it is tainted, where
    it came from, and how the prompt renders it."""

    model_config = ConfigDict(extra="forbid")

    trust: Literal["trusted", "untrusted"]
    tainted: bool
    source: BlockSource
    item: str | None = None
    rendered: str
    truncated: bool = False


@functools.cache
def _folds_to_bracket(ch: str) -> bool:
    """Whether a non-ASCII character's compatibility decomposition holds < or >."""
    return not ch.isascii() and any(c in "<>" for c in unicodedata.normalize("NFKD", ch))


def _escape_char(ch: str) -> str:
    named = _NAMED.get(ch)
    if named is not None:
        return named
    if ch == "\r" or ch in CONFUSABLE_BRACKETS or ch in INVISIBLE_CONTROLS or _folds_to_bracket(ch):
        return f"&#x{ord(ch):X};"
    return ch


def escape_untrusted(text: str) -> str:
    """Outside text made safe for an untrusted block: `&` first (so an entity in the input
    stays literal text), `<` and `>` as named entities; look-alike brackets, invisible
    controls and carriage returns as numeric ones. `unescape_untrusted` inverts it."""
    return "".join(_escape_char(ch) for ch in text)


def escape_attr(value: str, limit: int = ATTR_LIMIT) -> str:
    """An attribute value: its first `limit` characters escaped, quotes as `&quot;` and
    newlines as spaces, so it can neither end its attribute nor add another."""
    return escape_untrusted(value[:limit]).replace('"', "&quot;").replace("\n", " ")


def unescape_untrusted(text: str) -> str:
    """The inverse of `escape_untrusted` (tests and previews; agents read the escaped form)."""

    def one(match: re.Match[str]) -> str:
        named = match.group(1)
        return _UNNAMED[named] if named else chr(int(match.group(2), 16))

    return _ENTITY.sub(one, text)


def render_block(
    text: str,
    *,
    nonce: str,
    source: str,
    item: str | None,
    attrs: Mapping[str, str],
    trusted: bool,
) -> str:
    """Trusted text as it is; anything else inside an `<untrusted-data>` block whose open
    and close tags both carry the packet's nonce."""
    if trusted:
        return text
    if not _NONCE.fullmatch(nonce):
        raise ValueError(f"a block nonce matches {NONCE_RE}")
    bad = sorted(name for name in attrs if not _ATTR_NAME.fullmatch(name))
    if bad:
        raise ValueError(f"attribute names match {ATTR_NAME_RE}: {bad}")
    head = " ".join(
        [f'id="{nonce}"', f'source="{escape_attr(source)}"']
        + ([f'item="{escape_attr(item)}"'] if item else [])
        + [f'{name}="{escape_attr(value)}"' for name, value in sorted(attrs.items())]
    )
    return f'<untrusted-data {head}>\n{escape_untrusted(text)}\n</untrusted-data id="{nonce}">'


def packet_tainted(blocks: Iterable[Block]) -> bool:
    """A packet is tainted when any of its blocks is: computed, never remembered."""
    return any(block.tainted for block in blocks)


def comment_tainted(author: str, *, run_tainted: bool = False) -> bool:
    """Whether a task comment is tainted, from its author (comments keep no taint column):
    a write by an API key, never bound to a run, is tainted (R-31, as
    `agent_surface._taint`). A person's comment is not; a task token's follows its run's
    taint (P2-08): `run_tainted` is the stored taint of the run the token belonged to."""
    if author.startswith("task_token:"):
        return run_tainted
    return author.startswith("api_key:")


def truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    """The longest prefix of `text` within `max_bytes` UTF-8 bytes, and whether it was cut.
    Truncation comes before escaping, so an escape is never cut in half."""
    raw = text.encode("utf-8", errors="surrogatepass")
    if len(raw) <= max_bytes:
        return text, False
    return raw[:max_bytes].decode("utf-8", errors="ignore"), True


def render_task_prompt(
    preamble: str,
    instruction: str,
    sections: Sequence[tuple[str, Sequence[str]]],
    data_json: str,
) -> str:
    """A task packet's `prompt_text` (the daemon never builds prompts, R-25): the fixed
    preamble (trusted), the run's instruction, each non-empty section of rendered blocks
    under its heading, then the structured fields as JSON between `<packet>` markers. The
    JSON carries no outside text (all of it is in the blocks above); the caller writes its
    `<` characters as `\\u003c`."""
    parts = [preamble.strip(), instruction.strip()]
    parts += [f"## {heading}\n\n" + "\n\n".join(blocks) for heading, blocks in sections if blocks]
    parts.append(f"<packet>\n{data_json}\n</packet>")
    return "\n\n".join(parts) + "\n"


# The scopes a run's task token may hold, per kind (plan defaults; every RunKind, R-22):
# never `delegate` or `ingest`, and always cut to the issuing profile key's scopes (P0-14).
RUN_TOKEN_SCOPES: Final[dict[RunKind, frozenset[str]]] = {
    RunKind.ENRICH: frozenset({"tasks:read", "tasks:write", "context:read"}),
    RunKind.PLAN: frozenset({"tasks:read", "context:read"}),
    RunKind.TASK: frozenset(
        {"tasks:read", "tasks:write", "context:read", "knowledge:write", "drafts:write"}
    ),
    RunKind.PROPOSAL: frozenset({"tasks:read", "tasks:write", "context:read"}),
    RunKind.STUCK: frozenset({"tasks:read", "tasks:write", "context:read"}),
    RunKind.NOTIFY: frozenset({"tasks:read"}),
}


def run_token_scopes(kind: RunKind, key_scopes: frozenset[str]) -> frozenset[str]:
    """The kind's token scopes that the issuing key also holds."""
    return RUN_TOKEN_SCOPES[kind] & frozenset(key_scopes)


# --- Enrichment (P1-08, FR-4.4) ------------------------------------------------------------
#
# What the project agent fills, in one place with `enrichment_errors` above (one test table
# feeds all three): a first action when there is none or only the placeholder, acceptance
# criteria when there are none, and an estimate of human time only for a Human or Hybrid
# task without one. A pending label (NULL, R-08) asks for no estimate.

EnrichField = Literal["first_action", "acceptance_criteria", "estimate_minutes"]
FIRST_ACTION: Final = "first_action"
CRITERIA: Final = "acceptance_criteria"
ESTIMATE: Final = "estimate_minutes"
AGENT_LABEL_SOURCE: Final = "agent"  # a label the enrichment itself revised
PLACEHOLDER_SOURCE: Final = "placeholder"
REVISABLE_LABEL_SOURCES: Final = frozenset({"jev", "fallback"})  # the AI's own labels
AI_PART: Final = "AI part"  # the Hybrid split's fixed headings (no description column)
YOUR_PART: Final = "Your part"
TOO_LOW_AT: Final = 0.5  # plausibility levels 0..4: at or below "too low" (plan default)
TOO_HIGH_AT: Final = 3.5  # at or above "too high"
APPLIED_ROUTE: Final = "applied"
EnrichmentStatus = Literal[
    "pending", "running", "done", "agent_offline", "not_provisioned", "failed"
]
ENRICHMENT_BUSY: Final = frozenset({"pending", "running"})


class TaskSnapshot(BaseModel):
    """The task as the enrichment reads it (P1-08)."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    project_id: UUID
    title: str
    label: Label | None
    label_source: str | None
    status: str
    first_action: str | None
    first_action_source: str | None
    acceptance_criteria: str | None
    estimate_minutes: int | None
    version: int
    enrichment_status: str | None = None


class EnrichmentPatch(BaseModel):
    """What an enrichment writes: None leaves a field as it is."""

    model_config = ConfigDict(frozen=True)

    first_action: str | None = None
    acceptance_criteria: str | None = None
    estimate_minutes: int | None = None
    label: Label | None = None
    label_reason: str | None = None

    def is_empty(self) -> bool:
        return all(value is None for value in self.model_dump().values())


def _blank(text: str | None) -> bool:
    return text is None or not text.strip()


def missing_fields(t: TaskSnapshot) -> list[EnrichField]:
    """first_action when empty or still the placeholder; acceptance_criteria when empty;
    estimate_minutes when the label is human or hybrid and there is none. AI tasks and
    pending labels never list the estimate."""
    missing: list[EnrichField] = []
    if _blank(t.first_action) or t.first_action_source == PLACEHOLDER_SOURCE:
        missing.append("first_action")
    if _blank(t.acceptance_criteria):
        missing.append("acceptance_criteria")
    if t.label in ESTIMATED_LABELS and t.estimate_minutes is None:
        missing.append("estimate_minutes")
    return missing


def needs_enrichment(t: TaskSnapshot) -> bool:
    return bool(missing_fields(t)) and t.status != "done"


def estimate_follow_up(requested: Sequence[str], t: TaskSnapshot) -> bool:
    """After an enrichment that did not ask for the estimate, whether the task (as it is
    now) gets an estimate-only follow-up: it was labelled Human or Hybrid while that
    enrichment was pending or running (`enrich_on_update` starts nothing then), and still
    has no estimate. A label the enrichment itself revised (`label_source = "agent"`) is
    left to `enrich_on_update`, which its own `task.updated` reaches once it is `done`."""
    return (
        ESTIMATE not in requested
        and t.label_source != AGENT_LABEL_SOURCE
        and needs_enrichment(t)
        and ESTIMATE in missing_fields(t)
    )


class _SplitView(Protocol):
    @property
    def ai_portion(self) -> str: ...
    @property
    def human_portion(self) -> str: ...


class _RevisionView(Protocol):
    @property
    def label(self) -> Label: ...
    @property
    def reason(self) -> str: ...


class EnrichmentAnswerView(Protocol):
    """What `merge_enrichment` reads of an `EnrichmentResult` (skill_io.py)."""

    @property
    def first_action(self) -> str: ...
    @property
    def acceptance_criteria(self) -> Sequence[str]: ...
    @property
    def estimate_minutes(self) -> int | None: ...
    @property
    def label_revision(self) -> _RevisionView | None: ...
    @property
    def hybrid_split(self) -> _SplitView | None: ...


def may_revise_label(label: Label | None, label_source: str | None) -> bool:
    """The agent may revise a pending label or the AI's own (`jev`, `fallback`), never
    the user's or another agent's (FR-4.1, R-08)."""
    return label is None or label_source in REVISABLE_LABEL_SOURCES


def criteria_text(criteria: Sequence[str], split: _SplitView | None) -> str:
    """The criteria as `- ` lines; a Hybrid task's split follows them under the fixed
    headings `AI part` and `Your part`."""
    lines = [f"- {line}" for line in criteria]
    if split is not None:
        lines += ["", f"{AI_PART}: {split.ai_portion}", f"{YOUR_PART}: {split.human_portion}"]
    return "\n".join(lines)


def merge_enrichment(
    current: TaskSnapshot,
    res: EnrichmentAnswerView,
    *,
    requested: Sequence[str],
    estimate_range: tuple[int, int],
) -> EnrichmentPatch:
    """Fill only the fields that were requested AND are still missing now (a user edit in
    between wins, UX 9). A label revision applies over a pending label or the AI's own;
    revised to human or hybrid, a task without an estimate takes the result's. An estimate
    only for an effective human or hybrid label, and only within `estimate_range` (R-11)."""
    missing = set(missing_fields(current))
    wanted = missing & set(requested)
    revision = res.label_revision
    revised = (
        revision is not None
        and may_revise_label(current.label, current.label_source)
        and revision.label != current.label
    )
    label = revision.label if revised and revision is not None else current.label
    first_action = res.first_action if FIRST_ACTION in wanted else None
    criteria = None
    if CRITERIA in wanted:
        split = res.hybrid_split if label == "hybrid" else None
        criteria = criteria_text(res.acceptance_criteria, split)
    estimate = None
    low, high = estimate_range
    wants_estimate = ESTIMATE in wanted or revised
    if (
        wants_estimate
        and label in ESTIMATED_LABELS
        and current.estimate_minutes is None
        and res.estimate_minutes is not None
        and low <= res.estimate_minutes <= high
    ):
        estimate = res.estimate_minutes
    return EnrichmentPatch(
        first_action=first_action,
        acceptance_criteria=criteria,
        estimate_minutes=estimate,
        label=label if revised else None,
        label_reason=revision.reason if revised and revision is not None else None,
    )


class _ScoreView(Protocol):
    @property
    def score(self) -> float: ...


PlausibilityFlag = Literal["too_low", "too_high"]


def plausibility_flag(answer: _ScoreView | None, route: str) -> PlausibilityFlag | None:
    """Only an applied Score flags, and only at the outer levels: <= 0.5 too low, >= 3.5
    too high."""
    if answer is None or route != APPLIED_ROUTE:
        return None
    if answer.score <= TOO_LOW_AT:
        return "too_low"
    if answer.score >= TOO_HIGH_AT:
        return "too_high"
    return None


def enrichment_settled(status: str | None) -> bool:
    """Whether an enrichment has run and ended (not NULL, not pending or running)."""
    return status is not None and status not in ENRICHMENT_BUSY
