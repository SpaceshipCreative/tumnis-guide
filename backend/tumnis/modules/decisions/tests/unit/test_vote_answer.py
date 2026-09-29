"""vLLM answers from sampled votes (P1-02, FR-11.3): vLLM has no calibrated probabilities,
so the fallback samples each question several times and reads the vote shares."""

from __future__ import annotations

import math

import pytest


@pytest.mark.req("FR-11.3")
@pytest.mark.wp("P1-02")
@pytest.mark.xfail(strict=True, reason="spec:P1-02")
def test_vote_shares_and_confidence() -> None:
    """T-P1-02-04
    Five samples `[a, a, a, b, c]` of a three-option Choice give probabilities
    `.6 / .2 / .2`, the winner `a`, and confidence `(3 * .6 - 1) / 2 = 0.4`, the formula
    TypeSafe documents for Choice.
    """
    from tumnis.modules.decisions.catalog import ChoiceDef  # noqa: PLC0415
    from tumnis.modules.decisions.rules import vote_answer  # noqa: PLC0415

    question = ChoiceDef(instructions="Pick one.", criteria={"a": "A", "b": "B", "c": "C"})
    answer = vote_answer(question, ["a", "a", "a", "b", "c"])

    assert answer.type == "choice"
    assert answer.choice == "a"
    assert answer.probabilities == pytest.approx({"a": 0.6, "b": 0.2, "c": 0.2})
    assert math.isclose(answer.confidence, 0.4, abs_tol=1e-9)
