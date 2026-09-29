"""Edges of the routing and vote rules (P1-02): the clamps, the missing bands, the numbered
duplicate question and the vote's tie and invalid-sample handling."""

from __future__ import annotations

import pytest

from tumnis.modules.decisions.api import ChoiceAnswer, NoulAnswer, ScoreAnswer
from tumnis.modules.decisions.catalog import (
    CATALOGUE,
    ChoiceDef,
    DecisionPoint,
    NoulDef,
    ScoreDef,
)
from tumnis.modules.decisions.rules import (
    Route,
    Threshold,
    effective_threshold,
    low_route,
    main_answer,
    route,
    vote_answer,
)

pytestmark = [pytest.mark.req("FR-11.3", "FR-11.4"), pytest.mark.wp("P1-02")]


def test_primary_threshold_is_unchanged() -> None:
    t = Threshold(min_confidence=0.8, t_yes=0.9, t_no=0.1)
    assert effective_threshold(t, fallback=False) is t


def test_fallback_adds_the_margin_and_clamps() -> None:
    t = Threshold(min_confidence=0.95, t_yes=0.7, t_no=0.05, fallback_margin=0.1)
    eff = effective_threshold(t, fallback=True)
    assert eff.min_confidence == pytest.approx(0.99)
    assert eff.t_yes == pytest.approx(0.8)
    assert eff.t_no == pytest.approx(0.01)
    assert eff.fallback_margin == 0.1


def test_fallback_never_loosens_a_value_beyond_the_clamp() -> None:
    t = Threshold(min_confidence=0.995, t_no=0.005)
    eff = effective_threshold(t, fallback=True)
    assert eff.min_confidence == 0.995
    assert eff.t_no == 0.005


def test_fallback_leaves_absent_bands_absent() -> None:
    eff = effective_threshold(Threshold(t_no=0.05), fallback=True)
    assert eff.min_confidence is None
    assert eff.t_yes is None


def test_a_missing_confidence_floor_applies_only_a_certain_answer() -> None:
    spec = CATALOGUE[DecisionPoint.QUICK_ADD_LABEL]
    unsure = ChoiceAnswer(choice="ai", probabilities={"ai": 0.9, "human": 0.1}, confidence=0.99)
    sure = ChoiceAnswer(choice="ai", probabilities={"ai": 1.0, "human": 0.0}, confidence=1.0)
    assert route(spec, unsure, Threshold(), fallback=False) == (Route.REVIEW, "ai")
    assert route(spec, sure, Threshold(), fallback=False) == (Route.APPLY, "ai")


def test_a_missing_score_floor_applies_only_a_certain_answer() -> None:
    spec = CATALOGUE[DecisionPoint.BLOCKING_IMPACT]
    answer = ScoreAnswer(score=2.0, probabilities={"2": 0.9, "3": 0.1}, confidence=0.9)
    assert route(spec, answer, Threshold(), fallback=False) == (Route.DETERMINISTIC, None)


def test_a_noul_without_bands_goes_to_the_low_route() -> None:
    spec = CATALOGUE[DecisionPoint.ACTIONABILITY]
    assert route(spec, NoulAnswer(noul=0.5), Threshold(), fallback=False) == (Route.REVIEW, None)


def test_approval_need_without_a_no_band_always_requires_approval() -> None:
    spec = CATALOGUE[DecisionPoint.APPROVAL_NEED]
    got = route(spec, NoulAnswer(noul=0.0), Threshold(), fallback=False)
    assert got == (Route.APPROVAL_REQUIRED, None)


def test_a_fallback_answer_just_inside_the_primary_band_is_held_back() -> None:
    spec = CATALOGUE[DecisionPoint.ACTIONABILITY]
    t = Threshold(t_yes=0.85, t_no=0.15)
    answer = NoulAnswer(noul=0.86)
    assert route(spec, answer, t, fallback=False) == (Route.APPLY, True)
    assert route(spec, answer, t, fallback=True) == (Route.REVIEW, None)


def test_main_answer_is_the_named_question() -> None:
    answers = {"label": NoulAnswer(noul=0.1), "needs_decision": NoulAnswer(noul=0.9)}
    assert main_answer("label", answers) is answers["label"]


def test_main_answer_of_duplicate_is_the_likeliest_candidate() -> None:
    answers = {
        "dup_1": NoulAnswer(noul=0.2),
        "dup_2": NoulAnswer(noul=0.7),
        "dup_3": NoulAnswer(noul=0.4),
    }
    assert main_answer("dup", answers) is answers["dup_2"]


def test_main_answer_of_an_unanswered_question_is_an_error() -> None:
    with pytest.raises(KeyError):
        main_answer("dup", {"other": NoulAnswer(noul=0.5)})


def test_noul_vote_is_the_share_of_yes() -> None:
    answer = vote_answer(NoulDef(instructions="?"), ["yes", "yes", "no", "yes", "maybe"])
    assert answer == NoulAnswer(noul=0.75)


def test_score_vote_reads_levels_and_confidence() -> None:
    question = ScoreDef(instructions="?", criteria=["a", "b", "c"])
    answer = vote_answer(question, ["2", "2", "1", "2", "0"])
    assert answer.type == "score"
    assert answer.probabilities == pytest.approx({"0": 0.2, "1": 0.2, "2": 0.6})
    assert answer.score == pytest.approx(0.2 + 1.2)
    assert answer.confidence == pytest.approx((3 * 0.6 - 1) / 2)


def test_choice_vote_tie_goes_to_the_first_option() -> None:
    question = ChoiceDef(instructions="?", criteria={"a": "A", "b": "B"})
    answer = vote_answer(question, ["b", "a", "b", "a"])
    assert answer.type == "choice"
    assert answer.choice == "a"
    assert answer.confidence == pytest.approx(0.0)


def test_samples_outside_the_allowed_set_are_ignored() -> None:
    question = ChoiceDef(instructions="?", criteria={"a": "A", "b": "B"})
    answer = vote_answer(question, ["a", "zzz", "a", ""])
    assert answer.type == "choice"
    assert answer.probabilities == {"a": 1.0, "b": 0.0}


def test_no_valid_sample_is_an_error() -> None:
    question = ChoiceDef(instructions="?", criteria={"a": "A", "b": "B"})
    with pytest.raises(ValueError, match="allowed"):
        vote_answer(question, ["x", "y"])


@pytest.mark.parametrize(
    ("point", "expected"),
    [
        (DecisionPoint.QUICK_ADD_LABEL, Route.REVIEW),
        (DecisionPoint.FOCUS_ON_TASK, Route.DETERMINISTIC),
        (DecisionPoint.APPROVAL_NEED, Route.APPROVAL_REQUIRED),
    ],
)
def test_low_route_is_the_points_low_confidence_route(
    point: DecisionPoint, expected: Route
) -> None:
    assert low_route(CATALOGUE[point]) is expected
