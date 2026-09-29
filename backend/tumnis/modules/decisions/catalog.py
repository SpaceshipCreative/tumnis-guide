"""The decision catalogue (P1-01, FR-11.2, FR-11.4, Data flow rule 6): every typed question
Tumnis asks a decisions provider, the fields each may send, and the outbound builder.

Pure: no I/O, no clock. Question ids, option keys and levels are stable strings, because
recordings and the decision log (P1-02) key on them. `build_request` is the only way a
request is made: it drops every field the point does not whitelist, NFC-normalizes and
caps text, refuses bytes and missing required fields, and refuses a request larger than
Jev accepts (FR-11.9) before anything is sent.

`build_request` and friends live here rather than in `rules.py` (the plan's place for
them) because they need `unicodedata` and `json`, which the rules allow-list does not
include (T-P0-01-09).
"""

import json
import math
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tumnis.modules.decisions.rules import option_key


class DecisionPoint(StrEnum):
    QUICK_ADD_LABEL = "quick_add_label"  # Choice
    PROJECT_MATCH = "project_match"  # Choice over projects + unknown
    ACTIONABILITY = "actionability"  # Noul
    DUPLICATE = "duplicate"  # Noul per candidate
    APPROVAL_NEED = "approval_need"  # Noul
    BLOCKING_IMPACT = "blocking_impact"  # Score
    FOCUS_ON_TASK = "focus_on_task"  # Noul
    NUDGE_WARRANTED = "nudge_warranted"  # Noul
    ESTIMATE_PLAUSIBILITY = "estimate_plausibility"  # Score


# --- Question and field definitions ------------------------------------------------------

MAX_CHOICE_OPTIONS: Final = 255  # Jev's Choice limit (FR-11.2)
MIN_CHOICE_OPTIONS: Final = 2
MIN_SCORE_LEVELS: Final = 2
MAX_SCORE_LEVELS: Final = 10
MAX_PROJECT_OPTIONS: Final = MAX_CHOICE_OPTIONS - 1  # plus `unknown`
UNKNOWN: Final = "unknown"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FieldRule(_Frozen):
    kind: Literal["text", "int", "email", "domain", "text_list", "record_list"]
    max_chars: int | None = None  # per text value, after NFC normalization
    max_items: int | None = None  # for lists
    required: bool = True
    item_fields: dict[str, "FieldRule"] | None = None  # for record_list: each item's whitelist


class ChoiceDef(_Frozen):
    type: Literal["choice"] = "choice"
    instructions: str | dict[str, Any]
    criteria: dict[str, str | dict[str, Any]]  # 2..255 options

    @model_validator(mode="after")
    def _option_count(self) -> Self:
        if not MIN_CHOICE_OPTIONS <= len(self.criteria) <= MAX_CHOICE_OPTIONS:
            raise ValueError(f"a Choice has 2 to 255 options, not {len(self.criteria)}")
        return self


class ScoreDef(_Frozen):
    type: Literal["score"] = "score"
    instructions: str | dict[str, Any]
    criteria: list[str]  # 2..10 ordered levels, level 0 first

    @model_validator(mode="after")
    def _level_count(self) -> Self:
        if not MIN_SCORE_LEVELS <= len(self.criteria) <= MAX_SCORE_LEVELS:
            raise ValueError(f"a Score has 2 to 10 levels, not {len(self.criteria)}")
        return self


class NoulDef(_Frozen):
    type: Literal["noul"] = "noul"
    instructions: str | dict[str, Any]
    criteria: dict[Literal["true", "false"], str] | None = None


QuestionDef = Annotated[ChoiceDef | ScoreDef | NoulDef, Field(discriminator="type")]


class Abstain(_Frozen):
    kind: Literal["unknown_option", "confidence_floor", "noul_band"]
    option: str | None = None  # "unknown" for unknown_option


class QuestionSpec(_Frozen):
    point: DecisionPoint
    primitive: Literal["choice", "score", "noul"]
    main_question: str  # the question id P1-02 routes on (`dup` is `dup_1`..`dup_5`)
    fields: dict[str, FieldRule]  # the whitelist; nothing else is sent
    abstain: Abstain
    on_low_confidence: Literal["review", "deterministic", "require_approval"]
    timeout_ms: int  # per call, plan default


# --- Question text -----------------------------------------------------------------------

LABEL_OPTIONS: Final = ("human", "ai", "hybrid", UNKNOWN)
LABEL_INSTRUCTIONS: Final = (
    "Who should do this task? Human: it needs a decision, a relationship, a signature, "
    "physical presence, or judgment the user has reserved (see `reserved_judgments`). "
    "AI: it is fully specified, verifiable by tests or an artifact, and within a software "
    "agent's competence. Hybrid: part of it is AI work and part needs the human. "
    "Unknown: the title does not say enough to tell."
)
LABEL_CRITERIA: Final = {
    "human": "Needs a decision, a relationship, a signature, physical presence, or a "
    "judgment the user reserved.",
    "ai": "Fully specified, verifiable by tests or an artifact, and within a software "
    "agent's competence.",
    "hybrid": "Part of it is AI work and part needs the human.",
    UNKNOWN: "The title does not say enough to tell.",
}
LABEL_COMPANIONS: Final = {  # Nouls asked in the same call; they only feed the reason line
    "needs_decision": "The task needs the user to make a decision.",
    "needs_relationship": "The task depends on a personal relationship or conversation.",
    "needs_signature": "The task needs the user's signature or legal approval.",
    "needs_presence": "The task needs someone physically present.",
    "reserved_judgment": "The task touches a judgment listed in `reserved_judgments`.",
    "specified_verifiable": "The task is fully specified and its result can be verified "
    "by a test or an artifact.",
}

PROJECT_INSTRUCTIONS: Final = (
    "Which project does this item belong to? Compare the sender, participants, subject and "
    "body with each project's name, client, goal, domains, people and keywords. Unknown: no "
    "project fits, or the item does not say enough to tell."
)
PROJECT_UNKNOWN: Final = "No project fits, or the item does not say enough to tell."

ACTIONABLE_INSTRUCTIONS: Final = (
    "This item asks the user to do or decide something, or records a commitment."
)
ACTIONABLE_CRITERIA: Final = {
    "true": "It asks the user to do or decide something, or records a commitment.",
    "false": "It is informational: nothing is asked of the user and nothing is promised.",
}

MAX_DUPLICATE_CANDIDATES: Final = 5  # plan default
DUPLICATE_CRITERIA: Final = {
    "true": "Same work: doing one would complete the other.",
    "false": "Different work.",
}

GATED_INSTRUCTIONS: Final = (
    "The action `action_class`, described by `action_summary` on `target`, is one of the "
    "actions in `policy_gated`, or equivalent in effect to one of them."
)
GATED_CRITERIA: Final = {
    "true": "The action is gated by the policy, or has the same effect as a gated action.",
    "false": "The action is none of the gated actions and has none of their effects.",
}

IMPACT_INSTRUCTIONS: Final = (
    "How much work does this item block until the user answers it? Use the downstream task "
    "count, the human minutes waiting on it and the nearest due date."
)
IMPACT_LEVELS: Final = (
    "Blocks nothing else",
    "Blocks one small task",
    "Blocks several tasks or one important task",
    "Blocks a milestone or a client deliverable",
    "Blocks the whole project",
)

ON_TASK_INSTRUCTIONS: Final = "The recent `activity` is work on the task `task_title`."
ON_TASK_CRITERIA: Final = {
    "true": "The activity is work on this task.",
    "false": "The activity is something else.",
}

NUDGE_INSTRUCTIONS: Final = "A nudge now helps the user more than it interrupts them."
NUDGE_CRITERIA: Final = {
    "true": "A nudge now helps more than it interrupts.",
    "false": "A nudge now interrupts more than it helps.",
}

PLAUSIBILITY_INSTRUCTIONS: Final = (
    "How plausible is `estimate_minutes` of the user's time for this task, given its first "
    "action, acceptance criteria and the user's `history` of estimates and actual minutes?"
)
PLAUSIBILITY_LEVELS: Final = (
    "Far too low",
    "Somewhat low",
    "Plausible",
    "Somewhat high",
    "Far too high",
)


# --- The catalogue -----------------------------------------------------------------------


def _text(max_chars: int, *, required: bool = False) -> FieldRule:
    return FieldRule(kind="text", max_chars=max_chars, required=required)


def _int(*, required: bool = False) -> FieldRule:
    return FieldRule(kind="int", required=required)


def _texts(max_items: int, max_chars: int, *, required: bool = False) -> FieldRule:
    return FieldRule(kind="text_list", max_items=max_items, max_chars=max_chars, required=required)


EMAIL_MAX: Final = 254
DOMAIN_MAX: Final = 253
BODY_HEAD_MAX: Final = 2_000  # Data flow rule 6: never more

_CHOICE_ABSTAIN: Final = Abstain(kind="unknown_option", option=UNKNOWN)
_NOUL_ABSTAIN: Final = Abstain(kind="noul_band")
_SCORE_ABSTAIN: Final = Abstain(kind="confidence_floor")

# Each project offered to `project_match` becomes one Choice option described by these
# (capped) fields; nothing else about a project is sent.
PROJECT_OPTION_FIELDS: Final = {
    "name": _text(120, required=True),
    "client": _text(120),
    "goal": _text(200),
    "domains": FieldRule(kind="text_list", max_items=10, max_chars=DOMAIN_MAX, required=False),
    "people": FieldRule(kind="text_list", max_items=10, max_chars=EMAIL_MAX, required=False),
    "keywords": _texts(10, 60),
}

_SPECS: Final = (
    QuestionSpec(
        point=DecisionPoint.QUICK_ADD_LABEL,
        primitive="choice",
        main_question="label",
        fields={
            "title": _text(300, required=True),
            "parent_title": _text(300),
            "project_name": _text(120),
            "project_goal": _text(300),
            "reserved_judgments": _texts(10, 120),
        },
        abstain=_CHOICE_ABSTAIN,
        on_low_confidence="review",
        timeout_ms=800,  # plan default: the chip shows within 1 s of Enter (A1.1)
    ),
    QuestionSpec(
        point=DecisionPoint.PROJECT_MATCH,
        primitive="choice",
        main_question="project",
        fields={
            "kind": _text(10, required=True),
            "sender_email": FieldRule(kind="email", max_chars=EMAIL_MAX, required=False),
            "sender_name": _text(200),
            "subject": _text(300),
            "thread_subject": _text(300),
            "participants": FieldRule(
                kind="text_list", max_items=20, max_chars=EMAIL_MAX, required=False
            ),
            "body_head": _text(BODY_HEAD_MAX),
        },
        abstain=_CHOICE_ABSTAIN,
        on_low_confidence="review",
        timeout_ms=2_000,
    ),
    QuestionSpec(
        point=DecisionPoint.ACTIONABILITY,
        primitive="noul",
        main_question="actionable",
        fields={
            "kind": _text(10, required=True),
            "sender_email": FieldRule(kind="email", max_chars=EMAIL_MAX, required=False),
            "subject": _text(300),
            "body_head": _text(BODY_HEAD_MAX),
        },
        abstain=_NOUL_ABSTAIN,
        on_low_confidence="review",
        timeout_ms=2_000,
    ),
    QuestionSpec(
        point=DecisionPoint.DUPLICATE,
        primitive="noul",
        main_question="dup",
        fields={
            "proposal_title": _text(300, required=True),
            "proposal_first_action": _text(300),
            "candidates": FieldRule(
                kind="record_list",
                max_items=MAX_DUPLICATE_CANDIDATES,
                item_fields={
                    "title": _text(300, required=True),
                    "acceptance_criteria": _text(500),
                },
            ),
        },
        abstain=_NOUL_ABSTAIN,
        on_low_confidence="review",
        timeout_ms=2_000,
    ),
    QuestionSpec(
        point=DecisionPoint.APPROVAL_NEED,
        primitive="noul",
        main_question="gated",
        fields={
            "action_class": _text(60, required=True),
            "action_summary": _text(500, required=True),
            "target": _text(200),
            "policy_gated": _texts(20, 60),
            "policy_allowed": _texts(20, 60),
        },
        abstain=_NOUL_ABSTAIN,  # asymmetric band: P1-02 asks approval unless clearly no
        on_low_confidence="require_approval",
        timeout_ms=2_000,
    ),
    QuestionSpec(
        point=DecisionPoint.BLOCKING_IMPACT,
        primitive="score",
        main_question="impact",
        fields={
            "item_kind": _text(40, required=True),
            "item_summary": _text(500, required=True),
            "project_name": _text(120),
            "downstream_task_count": _int(),
            "downstream_human_minutes": _int(),
            "nearest_due_in_days": _int(),
        },
        abstain=_SCORE_ABSTAIN,
        on_low_confidence="deterministic",
        timeout_ms=2_000,
    ),
    QuestionSpec(
        point=DecisionPoint.FOCUS_ON_TASK,
        primitive="noul",
        main_question="on_task",
        fields={
            "task_title": _text(300, required=True),
            "first_action": _text(300),
            "repo_path": _text(200),
            "activity": _texts(10, 200, required=True),
            "minutes_since_activity": _int(),
        },
        abstain=_NOUL_ABSTAIN,
        on_low_confidence="deterministic",
        timeout_ms=1_500,
    ),
    QuestionSpec(
        point=DecisionPoint.NUDGE_WARRANTED,
        primitive="noul",
        main_question="nudge",
        fields={
            "level": _text(10, required=True),
            "event_kind": _text(20, required=True),
            "minutes_into_block": _int(),
            "task_status": _text(20),
            "recent_responses": _texts(5, 20),
            "minutes_since_last_nudge": _int(),
        },
        abstain=_NOUL_ABSTAIN,
        on_low_confidence="deterministic",
        timeout_ms=1_500,
    ),
    QuestionSpec(
        point=DecisionPoint.ESTIMATE_PLAUSIBILITY,
        primitive="score",
        main_question="plausibility",
        fields={
            "title": _text(300, required=True),
            "label": _text(10, required=True),
            "estimate_minutes": _int(required=True),
            "first_action": _text(300),
            "acceptance_criteria": _texts(6, 200),
            "history": FieldRule(
                kind="record_list",
                max_items=10,
                required=False,
                item_fields={
                    "title": _text(120, required=True),
                    "estimate": _int(),
                    "actual": _int(),
                },
            ),
        },
        abstain=_SCORE_ABSTAIN,
        on_low_confidence="deterministic",
        timeout_ms=2_000,
    ),
)

CATALOGUE: Final[Mapping[DecisionPoint, QuestionSpec]] = MappingProxyType(
    {spec.point: spec for spec in _SPECS}
)


# --- Errors ------------------------------------------------------------------------------


class DecisionInputError(ValueError):
    """The inputs cannot make a request. `code` is the stable name."""

    code = "decision_input_invalid"

    def __init__(self, point: DecisionPoint, field: str, message: str) -> None:
        super().__init__(f"{point.value}.{field}: {message}")
        self.point = point
        self.field = field


class MissingDecisionInput(DecisionInputError):  # noqa: N818  # the plan's name
    code = "decision_input_missing"


class InvalidDecisionInput(DecisionInputError):  # noqa: N818  # the plan's name
    code = "decision_input_invalid"


class TooManyOptions(DecisionInputError):  # noqa: N818  # the plan's name
    code = "too_many_options"


class DecisionRequestTooLarge(ValueError):  # noqa: N818  # the plan's name
    """The request would exceed Jev's token limits (FR-11.9); nothing was sent."""

    code = "decision_request_too_large"

    def __init__(self, point: DecisionPoint, estimated_tokens: int, limit: int) -> None:
        super().__init__(f"{point.value}: about {estimated_tokens} tokens, limit {limit}")
        self.point = point
        self.estimated_tokens = estimated_tokens
        self.limit = limit


# --- Field walker ------------------------------------------------------------------------


def _cap_text(value: str, max_chars: int | None) -> str:
    """NFC-normalize, then keep at most `max_chars` code points (the one truncation helper
    every field kind shares)."""
    normalized = unicodedata.normalize("NFC", value)
    return normalized if max_chars is None else normalized[:max_chars]


def _is_missing(value: Any) -> bool:
    return value is None or value == "" or (isinstance(value, list | tuple) and not value)


def _field(point: DecisionPoint, name: str, rule: FieldRule, value: Any) -> Any:
    """The capped, typed value to send for one whitelisted field."""
    if isinstance(value, bytes | bytearray | memoryview):
        raise InvalidDecisionInput(point, name, "bytes are never sent")
    if rule.kind in {"text", "email", "domain"}:
        if not isinstance(value, str):
            raise InvalidDecisionInput(point, name, "expected text")
        return _cap_text(value, rule.max_chars)
    if rule.kind == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            raise InvalidDecisionInput(point, name, "expected an integer")
        return value
    if not isinstance(value, list | tuple):
        raise InvalidDecisionInput(point, name, "expected a list")
    items = list(value)[: rule.max_items]
    if rule.kind == "text_list":
        texts = []
        for item in items:
            if not isinstance(item, str):
                raise InvalidDecisionInput(point, name, "expected a list of text")
            texts.append(_cap_text(item, rule.max_chars))
        return texts
    assert rule.item_fields is not None  # record_list  # noqa: S101
    records = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise InvalidDecisionInput(point, name, "expected a list of records")
        records.append(_fields(point, rule.item_fields, item, prefix=f"{name}[{index}]."))
    return records


def _fields(
    point: DecisionPoint, rules: Mapping[str, FieldRule], inputs: Mapping[str, Any], prefix: str
) -> dict[str, Any]:
    """Only the whitelisted keys of `inputs`, each capped; a missing optional field is left
    out (never sent as null)."""
    out: dict[str, Any] = {}
    for name, rule in rules.items():
        value = inputs.get(name)
        if _is_missing(value):
            if rule.required:
                raise MissingDecisionInput(point, prefix + name, "required")
            continue
        out[name] = _field(point, prefix + name, rule, value)
    return out


# --- Questions ---------------------------------------------------------------------------


def _project_options(inputs: Mapping[str, Any]) -> dict[str, str | dict[str, Any]]:
    point = DecisionPoint.PROJECT_MATCH
    projects = inputs.get("projects")
    if not isinstance(projects, Sequence) or isinstance(projects, str) or not projects:
        raise MissingDecisionInput(point, "projects", "at least one project to match")
    if len(projects) > MAX_PROJECT_OPTIONS:
        raise TooManyOptions(
            point, "projects", f"at most {MAX_PROJECT_OPTIONS} projects, got {len(projects)}"
        )
    options: dict[str, str | dict[str, Any]] = {}
    for position, project in enumerate(projects, start=1):
        if not isinstance(project, Mapping):
            raise InvalidDecisionInput(point, "projects", "expected a list of records")
        prefix = f"projects[{position - 1}]."
        options[option_key(position)] = _fields(point, PROJECT_OPTION_FIELDS, project, prefix)
    options[UNKNOWN] = PROJECT_UNKNOWN
    return options


def _nouls(texts: Mapping[str, str]) -> dict[str, QuestionDef]:
    return {qid: NoulDef(instructions=text) for qid, text in texts.items()}


def _label(inputs: Mapping[str, Any]) -> dict[str, QuestionDef]:
    label = ChoiceDef(instructions=LABEL_INSTRUCTIONS, criteria=dict(LABEL_CRITERIA))
    return {"label": label, **_nouls(LABEL_COMPANIONS)}


def _project(inputs: Mapping[str, Any]) -> dict[str, QuestionDef]:
    return {
        "project": ChoiceDef(instructions=PROJECT_INSTRUCTIONS, criteria=_project_options(inputs))
    }


def _duplicates(inputs: Mapping[str, Any]) -> dict[str, QuestionDef]:
    candidates = inputs.get("candidates")
    count = len(candidates) if isinstance(candidates, list | tuple) else 0
    if count == 0:
        raise MissingDecisionInput(DecisionPoint.DUPLICATE, "candidates", "at least one candidate")
    return {
        f"dup_{n}": NoulDef(
            instructions=f"Candidate {n} in `candidates` describes the same work as the "
            "proposal (`proposal_title`, `proposal_first_action`).",
            criteria=dict(DUPLICATE_CRITERIA),
        )
        for n in range(1, min(count, MAX_DUPLICATE_CANDIDATES) + 1)
    }


def _fixed(
    qid: str, question: QuestionDef
) -> Callable[[Mapping[str, Any]], dict[str, QuestionDef]]:
    return lambda _inputs: {qid: question}


_QUESTIONS: Final[Mapping[DecisionPoint, Callable[[Mapping[str, Any]], dict[str, QuestionDef]]]] = {
    DecisionPoint.QUICK_ADD_LABEL: _label,
    DecisionPoint.PROJECT_MATCH: _project,
    DecisionPoint.ACTIONABILITY: _fixed(
        "actionable",
        NoulDef(instructions=ACTIONABLE_INSTRUCTIONS, criteria=dict(ACTIONABLE_CRITERIA)),
    ),
    DecisionPoint.DUPLICATE: _duplicates,
    DecisionPoint.APPROVAL_NEED: _fixed(
        "gated", NoulDef(instructions=GATED_INSTRUCTIONS, criteria=dict(GATED_CRITERIA))
    ),
    DecisionPoint.BLOCKING_IMPACT: _fixed(
        "impact", ScoreDef(instructions=IMPACT_INSTRUCTIONS, criteria=list(IMPACT_LEVELS))
    ),
    DecisionPoint.FOCUS_ON_TASK: _fixed(
        "on_task", NoulDef(instructions=ON_TASK_INSTRUCTIONS, criteria=dict(ON_TASK_CRITERIA))
    ),
    DecisionPoint.NUDGE_WARRANTED: _fixed(
        "nudge", NoulDef(instructions=NUDGE_INSTRUCTIONS, criteria=dict(NUDGE_CRITERIA))
    ),
    DecisionPoint.ESTIMATE_PLAUSIBILITY: _fixed(
        "plausibility",
        ScoreDef(instructions=PLAUSIBILITY_INSTRUCTIONS, criteria=list(PLAUSIBILITY_LEVELS)),
    ),
}


def questions_for(point: DecisionPoint, inputs: Mapping[str, Any]) -> dict[str, QuestionDef]:
    """The questions asked for `point`. Only `project_match` (the options) and `duplicate`
    (one Noul per candidate) depend on the inputs."""
    return _QUESTIONS[point](inputs)


# --- The outbound request ----------------------------------------------------------------

MAX_REQUEST_TOKENS: Final = 60_000  # plan default margin under Jev's 64k (FR-11.9)
MAX_STATE_PLUS_QUESTION_TOKENS: Final = 30_000  # plan default margin under Jev's 32k


class OutboundRequest(_Frozen):
    point: DecisionPoint
    state: dict[str, Any]  # only whitelisted, capped fields
    questions: dict[str, QuestionDef]
    fields_sent: tuple[str, ...]  # sorted field names, logged by P1-02


def _tokens(value: Any) -> int:
    """ceil(UTF-8 bytes of the compact JSON / 4): conservative for any script."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return math.ceil(len(text.encode()) / 4)


def estimate_tokens(req: OutboundRequest) -> int:
    """The request's size in tokens, estimated conservatively (state and questions)."""
    return _tokens(req.model_dump(mode="json", include={"state", "questions"}))


def build_request(point: DecisionPoint, inputs: Mapping[str, Any]) -> OutboundRequest:
    """Drop every non-whitelisted key, NFC-normalize and truncate text, reject bytes,
    reject a missing required field (MissingDecisionInput), and refuse a request over the
    token limits (DecisionRequestTooLarge) before any call."""
    spec = CATALOGUE[point]
    state = _fields(point, spec.fields, inputs, prefix="")
    req = OutboundRequest(
        point=point,
        state=state,
        questions=questions_for(point, inputs),
        fields_sent=tuple(sorted(state)),
    )
    total = estimate_tokens(req)
    if total > MAX_REQUEST_TOKENS:
        raise DecisionRequestTooLarge(point, total, MAX_REQUEST_TOKENS)
    dumped = req.model_dump(mode="json", include={"state", "questions"})
    largest = max(_tokens(question) for question in dumped["questions"].values())
    if _tokens(dumped["state"]) + largest > MAX_STATE_PLUS_QUESTION_TOKENS:
        raise DecisionRequestTooLarge(
            point, _tokens(dumped["state"]) + largest, MAX_STATE_PLUS_QUESTION_TOKENS
        )
    return req
