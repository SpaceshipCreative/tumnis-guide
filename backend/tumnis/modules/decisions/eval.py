"""The calibration evaluation engine (P3-08, FR-11.5, Quality: decision quality), shared by
Settings > Calibration and `tumnis decisions eval`, so the offline script reproduces the
page's numbers by construction.

`evaluate` groups labeled decisions by point, provider and model (vLLM fallback answers
apart from Jev's, FR-11.3) and gives each group its labeled count, how many more it needs
before accuracy shows, the metrics at the threshold in force (and on explicit labels
only, since implicit ones overstate accuracy), and the sweep. It never suggests a
threshold: a human picks one (design decision 8).

A stored set is JSON Lines, one labeled decision per line: `decision_point`,
`model_version`, `provider`, `input_hash`, `answer`, `confidence`, `truth`, and
`explicit` (true when absent). No input text is stored, only its hash (Data flow rule 6,
SEC-6). `dump_set` writes the same bytes for the same rows, so a set's sha256 names it.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from tumnis.modules.decisions.rules import (
    DEFAULT_THRESHOLDS,
    MIN_LABELED,
    LabeledDecision,
    Metrics,
    SweepRow,
    Threshold,
    accuracy,
    confidence_bar,
    sweep,
)

FALLBACK_PROVIDER: Final = "vllm"


class Evaluation(BaseModel):
    """One point, provider and model: what the threshold in force did with its labeled
    decisions. `metrics`, `explicit_metrics` and `sweep` stay empty under 100 labeled."""

    model_config = ConfigDict(frozen=True)

    decision_point: str
    provider: str
    model_version: str
    labeled: int
    needed: int  # labeled outcomes still missing before accuracy shows
    threshold: float  # the threshold in force, on the logged confidence scale
    metrics: Metrics | None
    explicit_metrics: Metrics | None  # on labels a human gave, without the settled ones
    sweep: list[SweepRow]


def evaluate(
    rows: Sequence[LabeledDecision], thresholds: Mapping[str, Threshold]
) -> list[Evaluation]:
    """Evaluations sorted by point, provider and model. A point without a threshold in
    `thresholds` is judged at its plan default; vLLM answers at the fallback's stricter
    threshold."""
    groups: dict[tuple[str, str, str], list[LabeledDecision]] = {}
    for row in rows:
        groups.setdefault((row.decision_point, row.provider, row.model_version), []).append(row)
    evaluations = []
    for (point, provider, model), group in sorted(groups.items()):
        in_force = thresholds.get(point) or DEFAULT_THRESHOLDS.get(point, Threshold())
        bar = confidence_bar(in_force, fallback=provider == FALLBACK_PROVIDER)
        metrics = accuracy(group, bar)
        evaluations.append(
            Evaluation(
                decision_point=point,
                provider=provider,
                model_version=model,
                labeled=len(group),
                needed=max(0, MIN_LABELED - len(group)),
                threshold=bar,
                metrics=metrics,
                explicit_metrics=accuracy([r for r in group if r.explicit], bar),
                sweep=[] if metrics is None else sweep(group),
            )
        )
    return evaluations


class _SetLine(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decision_point: str
    model_version: str
    provider: str
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    answer: str
    confidence: float = Field(ge=0, le=1)
    truth: str
    explicit: bool = True


class InvalidSet(ValueError):  # noqa: N818  # reads as what it is: an invalid set
    """A stored set line that is not a labeled decision; the message names the line."""


def load_set(data: bytes) -> list[LabeledDecision]:
    """The labeled decisions of a stored set; blank lines are skipped."""
    rows = []
    for number, line in enumerate(data.decode().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = _SetLine.model_validate_json(line)
        except ValueError as exc:
            raise InvalidSet(f"line {number}: {exc}") from None
        rows.append(LabeledDecision(**parsed.model_dump()))
    return rows


def dump_set(rows: Sequence[LabeledDecision]) -> bytes:
    """The rows as a stored set: one line each, keys sorted, in the order given."""
    lines = [
        json.dumps(
            {
                "decision_point": r.decision_point,
                "model_version": r.model_version,
                "provider": r.provider,
                "input_hash": r.input_hash,
                "answer": r.answer,
                "confidence": r.confidence,
                "truth": r.truth,
                "explicit": r.explicit,
            },
            sort_keys=True,
        )
        for r in rows
    ]
    return "".join(f"{line}\n" for line in lines).encode()


def set_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
