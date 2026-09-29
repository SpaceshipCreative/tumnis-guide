"""The question catalogue (P1-01): every FR-11.4 decision point is typed data with an
abstain path, inside Jev's primitive limits."""

from __future__ import annotations

from typing import Any

import pytest

from tumnis.modules.decisions.tests._cases import POINTS, stored_inputs


def _projects(count: int) -> list[dict[str, Any]]:
    return [{"name": f"Project {n}", "goal": f"Goal {n}"} for n in range(count)]


@pytest.mark.req("FR-11.2")
@pytest.mark.wp("P1-01")
def test_every_fr_11_4_point_has_a_spec() -> None:
    """T-P1-01-01
    The catalogue holds exactly the nine FR-11.4 points, each with the primitive FR-11.4
    names; its main question is among the questions it asks; it whitelists at least one
    field and has a positive timeout.
    """
    from tumnis.modules.decisions.catalog import (  # noqa: PLC0415
        CATALOGUE,
        DecisionPoint,
        questions_for,
    )

    assert {point.value for point in DecisionPoint} == set(POINTS)
    assert {point.value for point in CATALOGUE} == set(POINTS)
    for point, spec in CATALOGUE.items():
        assert spec.point is point
        assert spec.primitive == POINTS[point.value], point
        assert spec.fields, point
        assert spec.timeout_ms > 0, point
        questions = questions_for(point, stored_inputs(point.value))
        main = [qid for qid in questions if qid.startswith(spec.main_question)]
        assert main, (point, spec.main_question, list(questions))
        assert {questions[qid].type for qid in main} == {spec.primitive}, point


@pytest.mark.req("FR-11.2")
@pytest.mark.wp("P1-01")
def test_every_point_defines_abstain() -> None:
    """T-P1-01-02
    Choice points offer an `unknown` option (and the abstain names it); Noul points
    abstain through a probability band; Score points through a confidence floor.
    """
    from tumnis.modules.decisions.catalog import CATALOGUE, questions_for  # noqa: PLC0415

    expected_kind = {"choice": "unknown_option", "noul": "noul_band", "score": "confidence_floor"}
    for point, spec in CATALOGUE.items():
        assert spec.abstain.kind == expected_kind[spec.primitive], point
        if spec.primitive == "choice":
            assert spec.abstain.option == "unknown", point
            question = questions_for(point, stored_inputs(point.value))[spec.main_question]
            assert question.type == "choice"
            assert "unknown" in question.criteria, point
        else:
            assert spec.abstain.option is None, point


@pytest.mark.req("FR-11.2")
@pytest.mark.wp("P1-01")
def test_primitive_limits_hold() -> None:
    """T-P1-01-03
    A Choice has at most 255 options: project match with 254 projects plus `unknown`
    passes, with 255 projects it is refused. Every Score has 2 to 10 levels, and a
    ScoreDef outside that range is refused.
    """
    from pydantic import ValidationError  # noqa: PLC0415

    from tumnis.modules.decisions.catalog import (  # noqa: PLC0415
        CATALOGUE,
        DecisionPoint,
        ScoreDef,
        TooManyOptions,
        questions_for,
    )

    inputs = stored_inputs("project_match")
    fits = questions_for(DecisionPoint.PROJECT_MATCH, {**inputs, "projects": _projects(254)})
    project = fits["project"]
    assert project.type == "choice"
    assert len(project.criteria) == 255
    assert list(project.criteria)[:2] == ["p01", "p02"]
    assert list(project.criteria)[-2:] == ["p254", "unknown"]

    with pytest.raises(TooManyOptions):
        questions_for(DecisionPoint.PROJECT_MATCH, {**inputs, "projects": _projects(255)})

    for point, spec in CATALOGUE.items():
        for question in questions_for(point, stored_inputs(point.value)).values():
            if question.type == "score":
                assert 2 <= len(question.criteria) <= 10, point
            if question.type == "choice":
                assert 2 <= len(question.criteria) <= 255, point
        assert spec.primitive in {"choice", "score", "noul"}
    for levels in (["only one"], [f"level {n}" for n in range(11)]):
        with pytest.raises(ValidationError):
            ScoreDef(instructions="Rate it", criteria=levels)
