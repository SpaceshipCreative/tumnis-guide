"""Calibration rules (P3-08, FR-11.5): outcome labels, accuracy once 100 labeled outcomes
exist, and what each threshold would have done."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tumnis.modules.decisions.rules import DecisionLogRow, HumanDecision, LabeledDecision

NOW = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
SETS = Path(__file__).resolve().parents[5] / "fixtures" / "decision_eval"


def _labeled(confidence: float, *, correct: bool, point: str = "project_match") -> LabeledDecision:
    return LabeledDecision(
        decision_point=point,
        model_version="jev-1.13.0",
        provider="jev",
        answer="p01",
        confidence=confidence,
        truth="p01" if correct else "p02",
    )


def _stored_set(point: str) -> list[LabeledDecision]:
    rows = []
    for line in (SETS / f"{point}.jsonl").read_text().splitlines():
        raw = json.loads(line)
        rows.append(
            LabeledDecision(
                decision_point=raw["decision_point"],
                model_version=raw["model_version"],
                provider=raw["provider"],
                answer=raw["answer"],
                confidence=raw["confidence"],
                truth=raw["truth"],
                input_hash=raw["input_hash"],
            )
        )
    return rows


@pytest.mark.req("FR-11.5")
@pytest.mark.wp("P3-08")
def test_accuracy_hidden_below_100_labeled() -> None:
    """T-P3-08-01
    99 labeled outcomes give no metrics (None); the 100th gives metrics over all 100.
    """
    from tumnis.modules.decisions.rules import MIN_LABELED, accuracy  # noqa: PLC0415

    rows = [_labeled(0.9, correct=True) for _ in range(99)]
    assert MIN_LABELED == 100
    assert accuracy(rows, 0.85) is None
    metrics = accuracy([*rows, _labeled(0.5, correct=False)], 0.85)
    assert metrics is not None
    assert metrics.n == 100
    assert metrics.auto_rate == pytest.approx(0.99)
    assert metrics.review_rate == pytest.approx(0.01)
    assert metrics.auto_precision == pytest.approx(1.0)
    assert metrics.overall_accuracy == pytest.approx(0.99)


@pytest.mark.req("FR-11.5")
@pytest.mark.wp("P3-08")
def test_metric_values_on_fixture() -> None:
    """T-P3-08-02
    The stored project-match set (120 rows): 50 at confidence 0.95 (48 right), 30 at 0.88
    (24 right), 20 at 0.72 (12 right), 20 at 0.55 (8 right). At 0.85, 80 of 120 would be
    auto-applied (2/3), 72 of those right (0.9), 40 go to review (1/3), and the model is
    right on 92 of 120. At 0.9, 50 are auto-applied (5/12), 48 right (0.96). At 0.99
    nothing is auto-applied, so there is no auto precision.
    """
    from tumnis.modules.decisions.rules import accuracy  # noqa: PLC0415

    rows = _stored_set("project_match")
    assert len(rows) == 120

    at_085 = accuracy(rows, 0.85)
    assert at_085 is not None
    assert at_085.n == 120
    assert at_085.auto_rate == pytest.approx(80 / 120)
    assert at_085.auto_precision == pytest.approx(72 / 80)
    assert at_085.review_rate == pytest.approx(40 / 120)
    assert at_085.overall_accuracy == pytest.approx(92 / 120)

    at_090 = accuracy(rows, 0.9)
    assert at_090 is not None
    assert at_090.auto_rate == pytest.approx(50 / 120)
    assert at_090.auto_precision == pytest.approx(48 / 50)
    assert at_090.overall_accuracy == pytest.approx(92 / 120)

    at_099 = accuracy(rows, 0.99)
    assert at_099 is not None
    assert at_099.auto_rate == 0
    assert at_099.auto_precision is None
    assert at_099.review_rate == 1


def _row(route: str, *, answer: str | None = "p01", age_days: float = 1.0) -> DecisionLogRow:
    return DecisionLogRow(
        decision_point="project_match",
        model_version="jev-1.13.0",
        provider="jev",
        route=route,
        answer=answer,
        confidence=0.9 if answer is not None else None,
        decided_at=NOW - timedelta(days=age_days),
        input_hash="ab" * 32,
    )


def _human(*, overridden: bool, value: str | None = None) -> HumanDecision:
    return HumanDecision(overridden=overridden, value=value, at=NOW - timedelta(hours=1))


# (case, row, human, expected truth or None when unlabeled, explicit)
LABEL_CASES = [
    ("review accepted", _row("review"), _human(overridden=False), "p01", True),
    ("review edited", _row("review"), _human(overridden=True, value="p02"), "p02", True),
    ("applied then overridden", _row("applied"), _human(overridden=True, value="p03"), "p03", True),
    ("applied, settled", _row("applied", age_days=8), None, "p01", False),
    ("applied, exactly settled", _row("applied", age_days=7), None, "p01", False),
    ("applied, unsettled", _row("applied", age_days=3), None, None, False),
    ("review undecided", _row("review", age_days=30), None, None, False),
    ("no answer", _row("review", answer=None), _human(overridden=True, value="p02"), None, False),
    ("noul rejected", _row("review", answer="yes"), _human(overridden=True), "no", True),
]


@pytest.mark.req("FR-11.5")
@pytest.mark.wp("P3-08")
def test_label_outcome_table() -> None:
    """T-P3-08-03
    A decided review item takes the human's answer (their edit, or the model's answer when
    they accepted it); an applied answer the human overrode takes the override; an applied
    answer left alone for 7 days (the plan default) counts as right, labeled implicitly;
    anything younger, an undecided review item and a decision without an answer are
    unlabeled. A yes or no the human rejected without a value is the other one.
    """
    from tumnis.modules.decisions.rules import label_outcome  # noqa: PLC0415

    for case, row, human, truth, explicit in LABEL_CASES:
        labeled = label_outcome(row, human, NOW)
        if truth is None:
            assert labeled is None, case
            continue
        assert labeled is not None, case
        assert labeled.truth == truth, case
        assert labeled.answer == row.answer, case
        assert labeled.explicit is explicit, case
        assert labeled.confidence == row.confidence, case
        assert labeled.decision_point == "project_match", case
        assert labeled.model_version == "jev-1.13.0", case
        assert labeled.provider == "jev", case
        assert labeled.input_hash == row.input_hash, case


_labeled_rows = st.lists(
    st.builds(
        _labeled,
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        correct=st.booleans(),
    ),
    min_size=1,
    max_size=150,
)


@pytest.mark.req("FR-11.5")
@pytest.mark.wp("P3-08")
@settings(max_examples=60, deadline=None)
@given(
    rows=_labeled_rows,
    thresholds=st.lists(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False), min_size=2, max_size=8
    ),
)
def test_sweep_is_monotonic(rows: list[LabeledDecision], thresholds: list[float]) -> None:
    """T-P3-08-04
    For any labeled rows and any increasing thresholds, the sweep gives one row per
    threshold in order, and raising the threshold never raises the auto-apply rate.
    """
    from tumnis.modules.decisions.rules import sweep  # noqa: PLC0415

    ordered = sorted(thresholds)
    swept = sweep(rows, ordered)
    assert [s.threshold for s in swept] == ordered
    rates = [s.auto_rate for s in swept]
    assert all(later <= earlier for earlier, later in pairwise(rates))
    assert all(s.n == len(rows) for s in swept)
