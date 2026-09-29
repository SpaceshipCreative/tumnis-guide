"""Routing a typed answer (P1-02, FR-11.3, FR-11.4): per decision point, a confident answer
is applied and anything else goes where the catalogue says (`on_low_confidence`); a vLLM
fallback answer is held to a stricter threshold; `approval_need` fails safe."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

if TYPE_CHECKING:
    from tumnis.modules.decisions.catalog import QuestionSpec

STEP = 0.001  # "just above" and "just below" the threshold
LOW_ROUTE = {
    "review": "review",
    "deterministic": "deterministic",
    "require_approval": "approval_required",
}

# (point, case) for every decision point: the cases its primitive has.
CHOICE_CASES = ("above", "below", "abstain")
SCORE_CASES = ("above", "below")
NOUL_CASES = ("yes_above", "yes_below", "no_below", "no_above")
APPROVAL_CASES = ("no_at", "no_above")
POINT_CASES = [
    ("quick_add_label", CHOICE_CASES),
    ("project_match", CHOICE_CASES),
    ("actionability", NOUL_CASES),
    ("duplicate", NOUL_CASES),
    ("approval_need", APPROVAL_CASES),
    ("blocking_impact", SCORE_CASES),
    ("focus_on_task", NOUL_CASES),
    ("nudge_warranted", NOUL_CASES),
    ("estimate_plausibility", SCORE_CASES),
]
TABLE = [
    pytest.param(point, case, id=f"{point}-{case}")
    for point, cases in POINT_CASES
    for case in cases
]


def _choice(winner: str, options: list[str], confidence: float) -> Any:
    from tumnis.modules.decisions.api import ChoiceAnswer  # noqa: PLC0415

    rest = [o for o in options if o != winner]
    p_max = 0.9
    share = (1.0 - p_max) / len(rest)
    probabilities = {o: (p_max if o == winner else share) for o in options}
    return ChoiceAnswer(choice=winner, probabilities=probabilities, confidence=confidence)


def _score(levels: int, confidence: float) -> Any:
    from tumnis.modules.decisions.api import ScoreAnswer  # noqa: PLC0415

    probabilities = {str(n): (1.0 if n == levels - 1 else 0.0) for n in range(levels)}
    return ScoreAnswer(score=float(levels - 1), probabilities=probabilities, confidence=confidence)


def _noul(value: float) -> Any:
    from tumnis.modules.decisions.api import NoulAnswer  # noqa: PLC0415

    return NoulAnswer(noul=value)


def _options(spec: QuestionSpec) -> list[str]:
    if spec.point == "project_match":
        return ["p01", "p02", "unknown"]
    return ["human", "ai", "hybrid", "unknown"]


@pytest.mark.req("FR-11.4")
@pytest.mark.wp("P1-02")
@pytest.mark.xfail(strict=True, reason="spec:P1-02")
@pytest.mark.parametrize(("point", "case"), TABLE)
def test_route_table(point: str, case: str) -> None:
    """T-P1-02-01
    Given `CATALOGUE[point]` and its default threshold, when `route` is called with
    synthetic answers at threshold +/- 0.001 and with the abstain option, then the routes
    match the routing table: just above applies (the winning option, the score, or the
    Noul's yes or no), just below goes to the point's `on_low_confidence` route, and the
    abstain option (`unknown`) never applies, however confident.
    """
    from tumnis.modules.decisions.catalog import CATALOGUE, DecisionPoint  # noqa: PLC0415
    from tumnis.modules.decisions.rules import DEFAULT_THRESHOLDS, Route, route  # noqa: PLC0415

    spec = CATALOGUE[DecisionPoint(point)]
    t = DEFAULT_THRESHOLDS[point]
    low = Route(LOW_ROUTE[spec.on_low_confidence])

    if spec.primitive == "choice":
        options = _options(spec)
        assert t.min_confidence is not None
        if case == "above":
            got = route(
                spec, _choice(options[0], options, t.min_confidence + STEP), t, fallback=False
            )
            assert got == (Route.APPLY, options[0])
        elif case == "below":
            got = route(
                spec, _choice(options[0], options, t.min_confidence - STEP), t, fallback=False
            )
            assert got == (low, options[0])  # shown as a suggestion
        else:
            got = route(spec, _choice("unknown", options, 0.99), t, fallback=False)
            assert got == (low, None)
    elif spec.primitive == "score":
        assert t.min_confidence is not None
        levels = 5
        if case == "above":
            got = route(spec, _score(levels, t.min_confidence + STEP), t, fallback=False)
            assert got == (Route.APPLY, float(levels - 1))
        else:
            got = route(spec, _score(levels, t.min_confidence - STEP), t, fallback=False)
            assert got == (low, None)
    elif point == "approval_need":
        assert t.t_no is not None
        assert t.t_yes is None  # no yes band: only a confident "not gated" applies
        if case == "no_at":
            assert route(spec, _noul(t.t_no), t, fallback=False) == (Route.APPLY, False)
        else:
            got = route(spec, _noul(t.t_no + STEP), t, fallback=False)
            assert got == (Route.APPROVAL_REQUIRED, None)
    else:
        assert t.t_yes is not None
        assert t.t_no is not None
        expected = {
            "yes_above": (t.t_yes + STEP, (Route.APPLY, True)),
            "yes_below": (t.t_yes - STEP, (low, None)),
            "no_below": (t.t_no - STEP, (Route.APPLY, False)),
            "no_above": (t.t_no + STEP, (low, None)),
        }
        value, want = expected[case]
        assert route(spec, _noul(value), t, fallback=False) == want


@st.composite
def _answers_for(draw: st.DrawFn, spec: QuestionSpec) -> Any:
    if spec.primitive == "noul":
        return _noul(draw(st.floats(0.0, 1.0)))
    from tumnis.modules.decisions.api import ChoiceAnswer, ScoreAnswer  # noqa: PLC0415

    keys = _options(spec) if spec.primitive == "choice" else [str(n) for n in range(5)]
    weights = draw(st.lists(st.floats(0.01, 1.0), min_size=len(keys), max_size=len(keys)))
    total = sum(weights)
    probabilities = {k: w / total for k, w in zip(keys, weights, strict=True)}
    confidence = draw(st.floats(0.0, 1.0))
    if spec.primitive == "choice":
        winner = max(probabilities, key=lambda k: probabilities[k])
        return ChoiceAnswer(choice=winner, probabilities=probabilities, confidence=confidence)
    score = sum(int(k) * p for k, p in probabilities.items())
    return ScoreAnswer(score=score, probabilities=probabilities, confidence=confidence)


@st.composite
def _case(draw: st.DrawFn) -> tuple[Any, Any, Any]:
    from tumnis.modules.decisions.catalog import CATALOGUE  # noqa: PLC0415
    from tumnis.modules.decisions.rules import Threshold  # noqa: PLC0415

    spec = draw(st.sampled_from(list(CATALOGUE.values())))
    level = st.floats(0.01, 0.99)
    margin = draw(st.floats(0.0, 0.5))
    if spec.primitive == "noul":
        t_no = draw(level)
        t_yes = None if spec.point == "approval_need" else draw(level)
        t = Threshold(t_yes=t_yes, t_no=t_no, fallback_margin=margin)
    else:
        t = Threshold(min_confidence=draw(level), fallback_margin=margin)
    return spec, draw(_answers_for(spec)), t


@pytest.mark.req("FR-11.3")
@pytest.mark.wp("P1-02")
@pytest.mark.xfail(strict=True, reason="spec:P1-02")
@settings(max_examples=300, deadline=None)
@given(_case())
def test_fallback_is_stricter(case: tuple[Any, Any, Any]) -> None:
    """T-P1-02-02
    For any answer (Choice and Score with valid distributions, Nouls) and any threshold in
    0.01..0.99, if the route with `fallback=True` is APPLY, the route with
    `fallback=False` is APPLY too: the vLLM fallback never applies what Jev's own
    threshold would not.
    """
    from tumnis.modules.decisions.rules import Route, route  # noqa: PLC0415

    spec, answer, t = case
    if route(spec, answer, t, fallback=True)[0] is Route.APPLY:
        assert route(spec, answer, t, fallback=False)[0] is Route.APPLY


@pytest.mark.req("FR-11.4")
@pytest.mark.wp("P1-02")
@pytest.mark.xfail(strict=True, reason="spec:P1-02")
def test_approval_need_fails_safe() -> None:
    """T-P1-02-03
    For `approval_need` with its default threshold, 0.05 yields APPLY(false) (not gated);
    0.051, 0.5 and 0.99 all yield APPROVAL_REQUIRED; no value ever yields APPLY(true),
    and a fallback answer is never more permissive.
    """
    from tumnis.modules.decisions.catalog import CATALOGUE, DecisionPoint  # noqa: PLC0415
    from tumnis.modules.decisions.rules import DEFAULT_THRESHOLDS, Route, route  # noqa: PLC0415

    spec = CATALOGUE[DecisionPoint.APPROVAL_NEED]
    t = DEFAULT_THRESHOLDS["approval_need"]
    assert route(spec, _noul(0.05), t, fallback=False) == (Route.APPLY, False)
    for value in (0.051, 0.5, 0.99):
        assert route(spec, _noul(value), t, fallback=False) == (Route.APPROVAL_REQUIRED, None)
    for step in range(101):
        for fallback in (False, True):
            got = route(spec, _noul(step / 100), t, fallback=fallback)
            assert got != (Route.APPLY, True)
