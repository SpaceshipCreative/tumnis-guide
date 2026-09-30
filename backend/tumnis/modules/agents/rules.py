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


def comment_tainted(author: str) -> bool:
    """Whether a task comment is tainted, from its author (comments keep no taint column):
    a write by an API key, never bound to a run, is tainted (R-31, as
    `agent_surface._taint`). A person's comment is not; a task token's follows its run's
    taint, which P2-08 propagates."""
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
