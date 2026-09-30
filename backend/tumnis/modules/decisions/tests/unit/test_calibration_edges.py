"""Edges of the calibration rules (P3-08, FR-11.5): the answer and value texts labels
compare, the confidence bar a threshold stands for, and an empty sweep."""

from __future__ import annotations

import pytest

from tumnis.modules.decisions.rules import (
    ChoiceAnswer,
    NoulAnswer,
    ScoreAnswer,
    Threshold,
    answer_text,
    confidence_bar,
    sweep,
    value_text,
)

pytestmark = [pytest.mark.req("FR-11.5"), pytest.mark.wp("P3-08")]


def test_answer_text_per_primitive() -> None:
    choice = ChoiceAnswer(choice="p02", probabilities={"p02": 1.0}, confidence=0.9)
    score = ScoreAnswer(score=2.6, probabilities={"2": 0.4, "3": 0.6}, confidence=0.5)
    assert answer_text(choice) == "p02"
    assert answer_text(score) == "3"
    assert answer_text(NoulAnswer(noul=0.5)) == "yes"
    assert answer_text(NoulAnswer(noul=0.49)) == "no"


@pytest.mark.parametrize(
    ("value", "text"),
    [(None, None), (True, "yes"), (False, "no"), (2, "2"), (1.6, "2"), ("hybrid", "hybrid")],
)
def test_value_text(value: object, text: str | None) -> None:
    assert value_text(value) == text


def test_confidence_bar_per_threshold_shape() -> None:
    assert confidence_bar(Threshold(min_confidence=0.85)) == pytest.approx(0.85)
    assert confidence_bar(Threshold(min_confidence=0.85), fallback=True) == pytest.approx(0.95)
    assert confidence_bar(Threshold(t_yes=0.85, t_no=0.15)) == pytest.approx(0.7)
    assert confidence_bar(Threshold(t_yes=0.9, t_no=0.2)) == pytest.approx(0.8)  # stricter band
    assert confidence_bar(Threshold(t_no=0.05)) == pytest.approx(0.9)  # approval_need: no yes band
    assert confidence_bar(Threshold(t_yes=0.8, t_no=0.2), fallback=True) == pytest.approx(0.8)
    assert confidence_bar(Threshold()) == 1.0


def test_sweep_without_rows_is_empty() -> None:
    assert sweep([]) == []
