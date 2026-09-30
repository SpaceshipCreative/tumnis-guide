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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Final, Literal, Protocol, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

NAME_RE: Final = r"^[a-z0-9][a-z0-9-]{0,62}$"  # profile and runner names; blocks shell tricks
SKILL_RE: Final = r"^[a-z][a-z0-9-]{0,40}$"
HEARTBEAT_S: Final = 15  # architecture: every 15 seconds
MISSED_BEATS: Final = 3
RESERVED_PROFILE_NAMES: Final = frozenset({"default", "root", "hermes", "tumnis"})
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


# --- Digests (P2-03, FR-13.1, FR-13.4) -------------------------------------------------------
#
# A digest entry sits at a position (tx, seq): the ID of the transaction that wrote it and a
# sequence within the table. A reader returns only entries whose transaction is below the
# oldest one still running, so a position it hands out never has an uncommitted entry
# behind it (see `agents.digest`).


class DigestCursorInvalid(ValueError):  # noqa: N818  # carries the problem code
    """A cursor that was never issued to this consumer (400 `invalid_cursor`)."""

    code = "invalid_cursor"


@dataclass(frozen=True, order=True)
class Pos:
    tx: int
    seq: int


ZERO_POS: Final = Pos(0, 0)


def resolve_start(acked: Pos, since: Pos | None, issued_max: Pos) -> tuple[Pos, Pos]:
    """Returns (new_acked, read_from).

    - since None: read from acked (the last digest may be sent again if its answer was
      lost);
    - since past everything issued: DigestCursorInvalid (forged, or another consumer's);
    - since at or past acked: acknowledge it and read from it;
    - since before acked (stale): read from acked; acknowledged entries never come again.
    """
    if since is None:
        return acked, acked
    if since > issued_max:
        raise DigestCursorInvalid("That cursor was not issued to this consumer")
    if since >= acked:
        return since, since
    return acked, acked


DigestKind = Literal[
    "label_override",
    "result_rejected",
    "result_accepted",
    "approval_decided",
    "question_answered",
    "estimate_vs_actual",
    "task_changed",
    "task_commented",
    "document_changed",
    "proposal_accepted",
    "context_linked",
    "focus_response",
    "focus_setting_changed",
]
DigestScope = Literal["project", "workspace"]
DIGEST_KINDS: Final[tuple[str, ...]] = get_args(DigestKind)
# Project entries of these kinds are also listed in the workspace digest (FR-13.4).
ALSO_IN_WORKSPACE: Final = frozenset({"label_override"})
# The events the digest subscribes to (P2-15 adds `focus.responded`).
DIGEST_EVENTS: Final = (
    "human.decided",
    "task.created",
    "task.status_changed",
    "task.commented",
    "document.added",
    "document.changed",
    "context_item.linked",
    "focus.level_changed",
)
# P1-07 names a person's label over the AI's `label_override`; a low-confidence label
# item decided in review (P1-13) is `label`, an override unless the proposal was kept.
_LABEL_ITEM_KINDS: Final = frozenset({"label", "label_override"})
_DEFERRED: Final = frozenset({"snooze"})  # comes back later: not a decision yet


@dataclass(frozen=True)
class TaskFacts:
    """What the subscriber reads of the task an event names (through tasks' api)."""

    task_id: UUID
    project_id: UUID
    estimate_minutes: int | None = None
    actual_minutes: int | None = None


@dataclass(frozen=True)
class DigestEvent:
    name: str
    payload: Mapping[str, Any]
    actor: str
    task: TaskFacts | None = None


@dataclass(frozen=True)
class EntrySpec:
    kind: str
    scope: DigestScope
    project_id: UUID | None
    task_id: UUID | None
    data: dict[str, Any]


def _uuid(value: object) -> UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except ValueError:
        return None


def _str(value: object) -> str | None:
    return None if value is None else str(value)


def _extra(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    found = payload.get("payload")
    return found if isinstance(found, Mapping) else {}


def digest_task_id(name: str, payload: Mapping[str, Any]) -> UUID | None:
    """The task whose facts `classify_event` needs for this event, if any."""
    if name == "human.decided":
        if payload.get("target_type") == "task":
            return _uuid(payload.get("target_id"))
        return _uuid(_extra(payload).get("task_id"))
    if name in {"task.status_changed", "focus.responded"}:
        return _uuid(payload.get("task_id"))
    return None


def _decided(p: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:  # noqa: PLR0911
    """(kind, data) for a human decision, or None when the digest does not carry it."""
    item_kind, decision = p.get("item_kind"), str(p.get("decision"))
    extra, reason = _extra(p), p.get("reason")
    if decision in _DEFERRED:
        return None
    if item_kind in _LABEL_ITEM_KINDS:
        if item_kind == "label" and decision == "accept":
            return None
        previous = p.get("previous")
        before = previous.get("label") if isinstance(previous, Mapping) else None
        return "label_override", {
            "from": before,
            "to": extra.get("value", decision),
            "reason": reason,
        }
    if item_kind == "result" and decision == "accept":
        return "result_accepted", {"run_id": _str(extra.get("run_id"))}
    if item_kind == "result" and decision == "reject":
        return "result_rejected", {
            "run_id": _str(extra.get("run_id")),
            "feedback": reason if reason is not None else extra.get("feedback"),
        }
    if item_kind == "approval" and decision in {"approve", "deny"}:
        return "approval_decided", {
            "action_class": extra.get("action_class"),
            "decision": decision,
            "reason": reason,
        }
    if item_kind == "question" and decision == "answer":
        return "question_answered", {
            "question": extra.get("question"),
            "answer": extra.get("answer", reason),
        }
    if item_kind == "proposal" and decision == "accept":
        return "proposal_accepted", {
            "proposal_id": _str(p.get("item_id")),
            "created_task_id": _str(extra.get("task_id")),
        }
    return None


def _project_entry(
    kind: str, project: UUID | None, task: UUID | None, data: dict[str, Any]
) -> list[EntrySpec]:
    """One project entry, or none when its project is unknown (the task is gone)."""
    if project is None:
        return []
    body = {"task_id": _str(task), **data} if task is not None else data
    return [EntrySpec(kind, "project", project, task, body)]


def _status_changed(p: Mapping[str, Any], facts: TaskFacts | None) -> list[EntrySpec]:
    project = facts.project_id if facts is not None else None
    task_id, to = _uuid(p.get("task_id")), p.get("to")
    change = {"change": "status", "from": p.get("from"), "to": to}
    specs = _project_entry("task_changed", project, task_id, change)
    if to == "done" and facts is not None and facts.actual_minutes is not None:
        actual = {
            "estimate_minutes": facts.estimate_minutes,
            "actual_minutes": facts.actual_minutes,
        }
        specs += _project_entry("estimate_vs_actual", project, task_id, actual)
    return specs


def _commented(p: Mapping[str, Any]) -> list[EntrySpec]:
    author_kind = str(p.get("author") or "").partition(":")[0] or "system"
    data = {
        "comment_id": _str(p.get("comment_id")),
        "author_kind": author_kind,
        "trusted": author_kind == "user",  # a person's words; anything else is untrusted
        "text": str(p.get("text") or ""),
    }
    return _project_entry(
        "task_commented", _uuid(p.get("project_id")), _uuid(p.get("task_id")), data
    )


def _document(name: str, p: Mapping[str, Any]) -> list[EntrySpec]:
    data = {
        "document_id": _str(p.get("document_id")),
        "change": name.partition(".")[2],
        "version": p.get("version_no"),
        "trust": p.get("trust"),
        "title": p.get("title"),
    }
    project = _uuid(p.get("project_id"))
    if project is None:  # the workspace knowledge base
        return [EntrySpec("document_changed", "workspace", None, None, data)]
    return _project_entry("document_changed", project, None, data)


def classify_event(event: DigestEvent) -> list[EntrySpec]:  # noqa: PLR0911
    """The digest entries an event makes (the plan's table), nothing else. Pure: the
    subscriber reads the task's facts first (`digest_task_id`) and passes them in."""
    p, facts = event.payload, event.task
    task_project = facts.project_id if facts is not None else None
    match event.name:
        case "human.decided":
            found = _decided(p)
            if found is None:
                return []
            project = _uuid(_extra(p).get("project_id")) or task_project
            return _project_entry(found[0], project, digest_task_id(event.name, p), found[1])
        case "task.created":
            doc = p.get("doc")
            title = doc.get("title") if isinstance(doc, Mapping) else None
            data = {"change": "created", "title": title, "label": p.get("label")}
            project = _uuid(p.get("project_id"))
            return _project_entry("task_changed", project, _uuid(p.get("task_id")), data)
        case "task.status_changed":
            return _status_changed(p, facts)
        case "task.commented":
            return _commented(p)
        case "document.added" | "document.changed":
            return _document(event.name, p)
        case "context_item.linked":
            data = {
                "context_item_id": _str(p.get("context_item_id")),
                "target_type": p.get("target_type"),
            }
            project, task_id = _uuid(p.get("project_id")), _uuid(p.get("task_id"))
            return _project_entry("context_linked", project, task_id, data)
        case "focus.responded":
            response = {"response": p.get("response")}
            return _project_entry("focus_response", task_project, _uuid(p.get("task_id")), response)
        case "focus.level_changed":
            data = {"from": p.get("from"), "to": p.get("to"), "scope": p.get("scope")}
            return [EntrySpec("focus_setting_changed", "workspace", None, None, data)]
        case _:
            return []


# --- Untrusted blocks (P2-02's rendering; the digest uses it first, P2-03) ------------------
#
# Outside text reaches an agent only inside a block it cannot close: after escaping it holds
# no literal `<` or `>` (nor a bracket that looks like one, nor an invisible control), and
# the block's id carries a nonce, so a closing tag forged in a later block cannot match.

CONFUSABLE_BRACKETS: Final = frozenset("＜＞﹤﹥‹›⟨⟩〈〉˂˃ᐸᐳ❮❯〈〉")  # noqa: RUF001
INVISIBLE_CONTROLS: Final = frozenset(
    {chr(c) for c in range(0xE0000, 0xE0080)}  # Unicode tag characters
    | {chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))}  # bidi controls
)


def escape_untrusted(text: str) -> str:
    """Order matters: '&' first, so an entity in the input stays literal text."""
    text = (
        text.replace("\r\n", "\n").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return "".join(
        f"&#x{ord(ch):X};" if ch in CONFUSABLE_BRACKETS or ch in INVISIBLE_CONTROLS else ch
        for ch in text
    )


def escape_attr(value: str, limit: int = 200) -> str:
    return escape_untrusted(value[:limit]).replace('"', "&quot;").replace("\n", " ")


def render_block(
    text: str,
    *,
    nonce: str,
    source: str,
    item: str | None,
    attrs: Mapping[str, str],
    trusted: bool,
) -> str:
    """Trusted text as is; anything else as an untrusted block
    `<untrusted-data id="u-<nonce>" source=... item=... k=v...>` ... `</untrusted-data
    id="u-<nonce>">`, its content escaped."""
    if trusted:
        return text
    head = " ".join(
        [f'id="{nonce}"', f'source="{escape_attr(source)}"']
        + ([f'item="{escape_attr(item)}"'] if item else [])
        + [f'{k}="{escape_attr(v)}"' for k, v in sorted(attrs.items())]
    )
    return f'<untrusted-data {head}>\n{escape_untrusted(text)}\n</untrusted-data id="{nonce}">'
