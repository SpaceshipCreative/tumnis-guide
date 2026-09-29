"""decisions pure rules: the typed answers, the label reason line, option keys for project
matching and model pinning (P1-01); thresholds, routing and vLLM vote answers (P1-02). No
I/O; nothing here reads the clock.

The question catalogue and the outbound payload builder live in `catalog.py`: they need
`unicodedata` and `json`, which the rules allow-list (T-P0-01-09) does not include. For the
same reason `input_hash` (sha256 over canonical JSON) lives there, and the routing
functions take the catalogue's specs and questions through the small protocols below
rather than importing them.
"""

import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Annotated, Any, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

# --- Typed answers (P1-01): what every decisions provider returns -----------------------
#
# Defined here, not in the adapter port, so the pure routing and vote rules can build and
# read them; `adapters/port.py` and `api.py` re-export them.


class _Answer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ChoiceAnswer(_Answer):
    type: Literal["choice"] = "choice"
    choice: str
    probabilities: dict[str, float]  # option key -> probability; sums to 1
    confidence: float


class ScoreAnswer(_Answer):
    type: Literal["score"] = "score"
    score: float  # expected level: the probability-weighted mean of the level indices
    probabilities: dict[str, float]  # level index ("0", "1", ...) -> probability; sums to 1
    confidence: float


class NoulAnswer(_Answer):
    type: Literal["noul"] = "noul"
    noul: float  # probability of yes; Nouls carry no confidence (routed on bands, P1-02)


TypedAnswer = Annotated[ChoiceAnswer | ScoreAnswer | NoulAnswer, Field(discriminator="type")]

# --- Label reason (FR-4.1): Jev returns no reason text ----------------------------------

HUMAN_SIGNALS: Final[dict[str, str]] = {  # companion Noul id -> reason phrase, in tie order
    "needs_signature": "Needs a signature",
    "needs_decision": "Needs your decision",
    "needs_relationship": "Depends on a conversation",
    "needs_presence": "Needs someone there in person",
    "reserved_judgment": "A judgment you reserved",
}
SIGNAL_FLOOR: Final = 0.5  # plan default: a companion counts from a yes probability of 0.5
HUMAN_FALLBACK: Final = "Needs your judgment"
AI_VERIFIABLE: Final = "Fully specified and verifiable"
AI_FALLBACK: Final = "Looks automatable"
HYBRID_PREFIX: Final = "Part automatable, part needs you: "
MAX_REASON_CHARS: Final = 80


def _human_reason(companions: Mapping[str, float]) -> str:
    best, best_value = HUMAN_FALLBACK, SIGNAL_FLOOR
    for signal, phrase in HUMAN_SIGNALS.items():
        value = companions.get(signal, 0.0)
        if value > best_value or (value == best_value and best == HUMAN_FALLBACK):
            best, best_value = phrase, value
    return best


def label_reason(label: Literal["human", "ai", "hybrid"], companions: Mapping[str, float]) -> str:
    """Pick the strongest companion signal that agrees with the label; <= 80 chars, one line.

    human: the highest of the five human companions at 0.5 or above, else `Needs your
    judgment`. ai: `Fully specified and verifiable` when `specified_verifiable` is at 0.5
    or above, else `Looks automatable`. hybrid: `Part automatable, part needs you: ` and
    the human reason, lower-cased. A missing companion counts as 0.
    """
    if label == "ai":
        verifiable = companions.get("specified_verifiable", 0.0) >= SIGNAL_FLOOR
        return AI_VERIFIABLE if verifiable else AI_FALLBACK
    human = _human_reason(companions)
    reason = human if label == "human" else HYBRID_PREFIX + human[0].lower() + human[1:]
    return reason[:MAX_REASON_CHARS]


# --- Project match option keys ---------------------------------------------------------
#
# Option keys are sent to the model, so they carry no data: `p01`..`p254` by position in
# the project list the caller passed, mapped back here. Project UUIDs never leave.

_OPTION_KEY: Final = re.compile(r"^p(\d{2,3})$")


def option_key(position: int) -> str:
    """1 -> "p01", 99 -> "p99", 100 -> "p100"."""
    if position < 1:
        raise ValueError("option positions start at 1")
    return f"p{position:02d}"


def option_position(key: str) -> int | None:
    """The 1-based position an option key names; None for `unknown` or anything else."""
    match = _OPTION_KEY.match(key)
    position = 0 if match is None else int(match.group(1))
    if position < 1 or option_key(position) != key:
        return None
    return position


# --- Model pinning (FR-11.2): always a versioned id, never an alias ---------------------

_PINNED: Final = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*-\d+\.\d+\.\d+$")


def is_pinned_model(model: str) -> bool:
    """`jev-1.13.0` is pinned; `jev-latest`, `jev` and `jev-1.13` are aliases that move."""
    return bool(_PINNED.match(model))


# --- Thresholds and routing (P1-02, FR-11.3, FR-11.4) -----------------------------------


class Threshold(BaseModel, frozen=True):
    min_confidence: float | None = None  # Choice, Score
    t_yes: float | None = None  # Noul: at or above means yes
    t_no: float | None = None  # Noul: at or below means no
    fallback_margin: float = 0.10  # plan default: added strictness for vLLM answers


class Route(StrEnum):
    APPLY = "applied"
    REVIEW = "review"
    DETERMINISTIC = "deterministic"
    APPROVAL_REQUIRED = "approval_required"


# Plan defaults, deliberately conservative (PRD risk table): most items go to review until
# P3-08 calibrates them. `approval_need` has no yes band.
DEFAULT_THRESHOLDS: Final[Mapping[str, Threshold]] = {
    "quick_add_label": Threshold(min_confidence=0.80),
    "project_match": Threshold(min_confidence=0.85),
    "actionability": Threshold(t_yes=0.85, t_no=0.15),
    "duplicate": Threshold(t_yes=0.90, t_no=0.10),
    "approval_need": Threshold(t_no=0.05),
    "blocking_impact": Threshold(min_confidence=0.60),
    "focus_on_task": Threshold(t_yes=0.80, t_no=0.20),
    "nudge_warranted": Threshold(t_yes=0.80, t_no=0.20),
    "estimate_plausibility": Threshold(min_confidence=0.70),
}


class _Abstain(Protocol):
    @property
    def option(self) -> str | None: ...


class SpecLike(Protocol):
    """What routing reads of a catalogue `QuestionSpec`."""

    @property
    def point(self) -> str: ...
    @property
    def primitive(self) -> Literal["choice", "score", "noul"]: ...
    @property
    def abstain(self) -> _Abstain: ...
    @property
    def on_low_confidence(self) -> Literal["review", "deterministic", "require_approval"]: ...


class QuestionLike(Protocol):
    """What `vote_answer` reads of a catalogue question definition."""

    @property
    def type(self) -> Literal["choice", "score", "noul"]: ...
    @property
    def criteria(self) -> Any: ...


def effective_threshold(t: Threshold, *, fallback: bool) -> Threshold:
    """Fallback: min_confidence + margin (cap 0.99), t_yes + margin (cap 0.99), t_no -
    margin (floor 0.01)."""
    raise NotImplementedError


def route(
    spec: SpecLike, answer: TypedAnswer, t: Threshold, *, fallback: bool
) -> tuple[Route, Any]:
    """Returns the route and the value to apply (label, project key, bool, score)."""
    raise NotImplementedError


def vote_answer(q: QuestionLike, samples: Sequence[str]) -> TypedAnswer:
    """Choice/Score: probabilities = vote shares; confidence = (k * p_max - 1) / (k - 1);
    Score value = sum(level * p). Noul: noul = share of 'yes'."""
    raise NotImplementedError
