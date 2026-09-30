"""decisions pure rules: the typed answers, the label reason line, option keys for project
matching and model pinning (P1-01); thresholds, routing and vLLM vote answers (P1-02);
outcome labels and calibration metrics (P3-08). No I/O; nothing here reads the clock.

The question catalogue and the outbound payload builder live in `catalog.py`: they need
`unicodedata` and `json`, which the rules allow-list (T-P0-01-09) does not include. For the
same reason `input_hash` (sha256 over canonical JSON) lives there, and the routing
functions take the catalogue's specs and questions through the small protocols below
rather than importing them.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
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


_CAP: Final = 0.99
_FLOOR: Final = 0.01
_LOW_ROUTE: Final = {
    "review": Route.REVIEW,
    "deterministic": Route.DETERMINISTIC,
    "require_approval": Route.APPROVAL_REQUIRED,
}


def _stricter_up(value: float | None, margin: float) -> float | None:
    """A bar that must be met from above, raised by the margin (cap 0.99), never lowered
    even when the stored value is already above the cap."""
    return None if value is None else max(value, min(value + margin, _CAP))


def _stricter_down(value: float | None, margin: float) -> float | None:
    """A bar that must be met from below, lowered by the margin (floor 0.01), never raised."""
    return None if value is None else min(value, max(value - margin, _FLOOR))


def effective_threshold(t: Threshold, *, fallback: bool) -> Threshold:
    """Fallback: min_confidence + margin (cap 0.99), t_yes + margin (cap 0.99), t_no -
    margin (floor 0.01). A value already beyond a cap or floor is kept, so the fallback
    is never looser than the primary."""
    if not fallback:
        return t
    return t.model_copy(
        update={
            "min_confidence": _stricter_up(t.min_confidence, t.fallback_margin),
            "t_yes": _stricter_up(t.t_yes, t.fallback_margin),
            "t_no": _stricter_down(t.t_no, t.fallback_margin),
        }
    )


def route(  # noqa: PLR0911  # one return per row of the plan's routing table
    spec: SpecLike, answer: TypedAnswer, t: Threshold, *, fallback: bool
) -> tuple[Route, Any]:
    """Returns the route and the value to apply (label, project key, bool, score)."""
    eff = effective_threshold(t, fallback=fallback)
    low = _LOW_ROUTE[spec.on_low_confidence]
    if isinstance(answer, ChoiceAnswer):
        if answer.choice == spec.abstain.option:
            return low, None
        floor = 1.0 if eff.min_confidence is None else eff.min_confidence
        if answer.confidence >= floor:
            return Route.APPLY, answer.choice
        return low, answer.choice  # kept as a suggestion for the reviewer
    if isinstance(answer, ScoreAnswer):
        floor = 1.0 if eff.min_confidence is None else eff.min_confidence
        if answer.confidence >= floor:
            return Route.APPLY, answer.score
        return low, None
    if spec.point == "approval_need":
        if eff.t_no is not None and answer.noul <= eff.t_no:
            return Route.APPLY, False  # confidently not gated
        return Route.APPROVAL_REQUIRED, None
    if eff.t_yes is not None and answer.noul >= eff.t_yes:
        return Route.APPLY, True
    if eff.t_no is not None and answer.noul <= eff.t_no:
        return Route.APPLY, False
    return low, None


def low_route(spec: SpecLike) -> Route:
    """Where a decision goes without a usable answer (every provider failed): the point's
    `on_low_confidence`."""
    return _LOW_ROUTE[spec.on_low_confidence]


def main_answer(main_question: str, answers: Mapping[str, TypedAnswer]) -> TypedAnswer:
    """The answer `route` reads: the one named by the spec's `main_question`. `duplicate`
    asks one Noul per candidate (`dup_1`, `dup_2`, ...) and routes on the likeliest one."""
    if main_question in answers:
        return answers[main_question]
    prefix = main_question + "_"
    numbered = [a for qid, a in answers.items() if qid.startswith(prefix)]
    nouls = [a for a in numbered if isinstance(a, NoulAnswer)]
    if not nouls:
        raise KeyError(main_question)
    return max(nouls, key=lambda a: a.noul)


def vote_answer(q: QuestionLike, samples: Sequence[str]) -> TypedAnswer:
    """Choice/Score: probabilities = vote shares; confidence = (k * p_max - 1) / (k - 1);
    Score value = sum(level * p). Noul: noul = share of 'yes'. Samples outside the allowed
    set are ignored; ValueError when none is valid."""
    if q.type == "noul":
        allowed: list[str] = ["yes", "no"]
    elif q.type == "score":
        allowed = [str(n) for n in range(len(q.criteria))]
    else:
        allowed = list(q.criteria)
    valid = [s for s in samples if s in allowed]
    if not valid:
        raise ValueError("no sample is one of the allowed answers")
    shares = {option: valid.count(option) / len(valid) for option in allowed}
    if q.type == "noul":
        return NoulAnswer(noul=shares["yes"])
    p_max = max(shares.values())
    k = len(allowed)
    confidence = (k * p_max - 1) / (k - 1)
    if q.type == "score":
        score = sum(int(level) * share for level, share in shares.items())
        return ScoreAnswer(score=score, probabilities=shares, confidence=confidence)
    winner = next(option for option in allowed if shares[option] == p_max)  # ties: criteria order
    return ChoiceAnswer(choice=winner, probabilities=shares, confidence=confidence)


# --- Generation slot output (P1-03, FR-11.8): one short line or nothing -----------------

_LIST_MARKER: Final = re.compile(r"^(?:[-*\u2022>#]+|\d+[.)])\s+")
# Straight, curly and back quotes and markdown emphasis around the whole line.
_WRAPPERS: Final = "\"'`*_\u201c\u201d\u2018\u2019"
_SENTENCE_END: Final = re.compile(r"(?<=[.!?])\s+")
_TRAILING_CUT: Final = " ,;:-\u2013\u2014"  # dangling punctuation after a cut, dashes too


def one_line(text: str, max_chars: int) -> str | None:
    """The first line of `text` that holds a letter or digit, without a list marker or
    wrapping quotes, cut to its first sentence and to `max_chars` at a word boundary.
    None when nothing is left: empty output, or output that is only punctuation."""
    line = next((ln for ln in text.splitlines() if any(ch.isalnum() for ch in ln)), None)
    if line is None:
        return None
    line = _LIST_MARKER.sub("", line.strip()).strip(_WRAPPERS).strip()
    line = _SENTENCE_END.split(line, maxsplit=1)[0]
    if len(line) > max_chars:
        cut = line[:max_chars]
        line = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(_TRAILING_CUT)
    return line if any(ch.isalnum() for ch in line) else None


# --- Calibration (P3-08, FR-11.5): outcome labels and what each threshold would do -------

MIN_LABELED: Final = 100  # FR-11.5: accuracy shows once 100 labeled outcomes exist
SETTLE_DAYS: Final = 7  # plan default: an applied answer left alone this long counts as right
SWEEP: Final = (0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95)


class Outcome(StrEnum):
    auto_applied = "auto_applied"
    sent_to_review = "sent_to_review"
    overridden = "overridden"
    confirmed = "confirmed"


@dataclass(frozen=True)
class DecisionLogRow:
    """What labeling reads of one `decision_log` row."""

    decision_point: str
    model_version: str
    provider: str
    route: str  # the row's outcome: applied, review, deterministic or approval_required
    answer: str | None  # the main answer as text (`answer_text`); None without an answer
    confidence: float | None
    decided_at: datetime
    input_hash: str = ""


@dataclass(frozen=True)
class HumanDecision:
    """What the human did with the decision (`record_outcome`)."""

    overridden: bool
    value: str | None  # the value they chose as text (`value_text`), None when they gave none
    at: datetime


@dataclass(frozen=True)
class LabeledDecision:
    decision_point: str
    model_version: str
    provider: str
    answer: str
    confidence: float
    truth: str  # from the human's decision
    explicit: bool = True  # False: labeled only because nobody overrode it in time
    input_hash: str = ""


class Metrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    n: int
    auto_rate: float
    auto_precision: float | None  # None when nothing would be auto-applied
    review_rate: float
    overall_accuracy: float


class SweepRow(Metrics):
    threshold: float


def answer_text(answer: TypedAnswer) -> str:
    """The main answer as the text a label compares: a Choice's option, a Score's nearest
    level, a Noul's `yes` (at or above 0.5) or `no`."""
    if isinstance(answer, ChoiceAnswer):
        return answer.choice
    if isinstance(answer, ScoreAnswer):
        return str(round(answer.score))
    return "yes" if answer.noul >= 0.5 else "no"  # noqa: PLR2004  # the Noul's midpoint


def value_text(value: Any) -> str | None:
    """A human's chosen value as the same text: booleans as yes or no, numbers as the
    nearest level, anything else as its string."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int | float):
        return str(round(value))
    return str(value)


def outcome_of(row: DecisionLogRow, human: HumanDecision | None) -> Outcome:
    """What happened to the decision: the human overrode or confirmed it, or, before any
    human act, it was auto-applied or sent to review (or to a deterministic rule or an
    approval, which count as review)."""
    if human is not None:
        return Outcome.overridden if human.overridden else Outcome.confirmed
    return Outcome.auto_applied if row.route == Route.APPLY else Outcome.sent_to_review


_OPPOSITE: Final = {"yes": "no", "no": "yes"}


def _override_truth(answer: str, value: str | None) -> str:
    """The right answer when the human overrode `answer`: the value they chose; without
    one (a rejection), the other of yes and no, or simply not the answer."""
    if value is not None and value != answer:
        return value
    return _OPPOSITE.get(answer, f"not:{answer}")


def label_outcome(
    row: DecisionLogRow, human: HumanDecision | None, now: datetime, settle_days: int = SETTLE_DAYS
) -> LabeledDecision | None:
    """The decision with its truth, or None while it has none (FR-11.5):
    a human's decision is the truth (their override, or the answer they kept); an answer
    applied and not overridden within `settle_days` counts as right (an implicit label);
    anything else, and a decision without an answer, is unlabeled."""
    if row.answer is None or row.confidence is None:
        return None
    outcome = outcome_of(row, human)
    if outcome is Outcome.overridden and human is not None:
        truth, explicit = _override_truth(row.answer, human.value), True
    elif outcome is Outcome.confirmed:
        truth, explicit = row.answer, True
    elif outcome is Outcome.auto_applied and now - row.decided_at >= timedelta(days=settle_days):
        truth, explicit = row.answer, False
    else:
        return None
    return LabeledDecision(
        decision_point=row.decision_point,
        model_version=row.model_version,
        provider=row.provider,
        answer=row.answer,
        confidence=row.confidence,
        truth=truth,
        explicit=explicit,
        input_hash=row.input_hash,
    )


def _metrics(rows: Sequence[LabeledDecision], threshold: float) -> Metrics:
    """What `threshold` would have done with `rows` (at least one): the share it would
    auto-apply, how often those were right, the share left to review, and how often the
    model was right overall."""
    n = len(rows)
    auto = [r for r in rows if r.confidence >= threshold]
    right_auto = sum(r.answer == r.truth for r in auto)
    return Metrics(
        n=n,
        auto_rate=len(auto) / n,
        auto_precision=right_auto / len(auto) if auto else None,
        review_rate=(n - len(auto)) / n,
        overall_accuracy=sum(r.answer == r.truth for r in rows) / n,
    )


def accuracy(rows: Sequence[LabeledDecision], threshold: float) -> Metrics | None:
    """Metrics at `threshold`; None under `MIN_LABELED` labeled rows (FR-11.5)."""
    if len(rows) < MIN_LABELED:
        return None
    return _metrics(rows, threshold)


def sweep(rows: Sequence[LabeledDecision], thresholds: Sequence[float] = SWEEP) -> list[SweepRow]:
    """The metrics at each threshold, in the order given; empty without rows. It shows,
    and suggests nothing: a human picks the threshold (design decision 8)."""
    if not rows:
        return []
    return [SweepRow(threshold=t, **_metrics(rows, t).model_dump()) for t in thresholds]


def confidence_bar(t: Threshold, *, fallback: bool = False) -> float:
    """The threshold on the logged confidence scale that `accuracy` compares with: a
    Choice's or Score's `min_confidence`; for a Noul (confidence = |noul - 0.5| * 2) the
    stricter of its yes and no bands, `2 * t_yes - 1` and `1 - 2 * t_no`. The fallback's
    stricter threshold for vLLM answers. 1.0 when nothing is ever auto-applied."""
    eff = effective_threshold(t, fallback=fallback)
    if eff.min_confidence is not None:
        return eff.min_confidence
    bands = [2 * eff.t_yes - 1] if eff.t_yes is not None else []
    if eff.t_no is not None:
        bands.append(1 - 2 * eff.t_no)
    return round(max(bands), 6) if bands else 1.0
