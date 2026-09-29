"""Edges of the catalogue, the outbound builder and the pure rules beyond the spec table
(P1-01): every refusal names its field, and option keys and model pins round-trip."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from tumnis.modules.decisions.catalog import (
    MAX_REQUEST_TOKENS,
    MAX_STATE_PLUS_QUESTION_TOKENS,
    ChoiceDef,
    DecisionPoint,
    DecisionRequestTooLarge,
    InvalidDecisionInput,
    MissingDecisionInput,
    build_request,
    questions_for,
)
from tumnis.modules.decisions.rules import is_pinned_model, option_key, option_position
from tumnis.modules.decisions.tests._cases import stored_inputs

LABEL = DecisionPoint.QUICK_ADD_LABEL


@pytest.mark.req("Data flow rule 6")
@pytest.mark.wp("P1-01")
@pytest.mark.parametrize(
    ("point", "change", "error", "field"),
    [
        (LABEL, {"title": None}, MissingDecisionInput, "title"),
        (LABEL, {"title": ""}, MissingDecisionInput, "title"),
        (LABEL, {"title": b"bytes"}, InvalidDecisionInput, "title"),
        (LABEL, {"title": 42}, InvalidDecisionInput, "title"),
        (LABEL, {"reserved_judgments": "pricing"}, InvalidDecisionInput, "reserved_judgments"),
        (LABEL, {"reserved_judgments": ["ok", 3]}, InvalidDecisionInput, "reserved_judgments"),
        (
            DecisionPoint.ESTIMATE_PLAUSIBILITY,
            {"estimate_minutes": True},
            InvalidDecisionInput,
            "estimate_minutes",
        ),
        (
            DecisionPoint.ESTIMATE_PLAUSIBILITY,
            {"estimate_minutes": "20"},
            InvalidDecisionInput,
            "estimate_minutes",
        ),
        (
            DecisionPoint.ESTIMATE_PLAUSIBILITY,
            {"history": ["not a record"]},
            InvalidDecisionInput,
            "history",
        ),
        (
            DecisionPoint.ESTIMATE_PLAUSIBILITY,
            {"history": [{"estimate": 3}]},
            MissingDecisionInput,
            "history[0].title",
        ),
        (DecisionPoint.DUPLICATE, {"candidates": []}, MissingDecisionInput, "candidates"),
        (DecisionPoint.PROJECT_MATCH, {"projects": []}, MissingDecisionInput, "projects"),
        (DecisionPoint.PROJECT_MATCH, {"projects": "Acme"}, MissingDecisionInput, "projects"),
        (DecisionPoint.PROJECT_MATCH, {"projects": ["Acme"]}, InvalidDecisionInput, "projects"),
        (
            DecisionPoint.PROJECT_MATCH,
            {"projects": [{"goal": "x"}]},
            MissingDecisionInput,
            "projects[0].name",
        ),
    ],
)
def test_bad_inputs_are_refused_naming_the_field(
    point: DecisionPoint, change: dict[str, Any], error: type[Exception], field: str
) -> None:
    """A missing required field, bytes, a wrong type or an empty list is refused before
    anything is built, and the error names the field."""
    with pytest.raises(error) as refused:
        build_request(point, {**stored_inputs(point.value), **change})
    assert refused.value.field == field  # type: ignore[attr-defined]
    assert refused.value.code.startswith(("decision_input_", "too_many"))  # type: ignore[attr-defined]


@pytest.mark.req("Data flow rule 6")
@pytest.mark.wp("P1-01")
def test_lists_are_capped_and_empty_optionals_left_out() -> None:
    """Lists keep at most their cap of items; an empty optional field is not sent; only
    the first five duplicate candidates are asked about."""
    inputs = {
        **stored_inputs("quick_add_label"),
        "reserved_judgments": [f"j{n}" for n in range(30)],
    }
    inputs["parent_title"] = ""
    req = build_request(LABEL, inputs)
    assert len(req.state["reserved_judgments"]) == 10
    assert "parent_title" not in req.state

    candidates = [{"title": f"Task {n}"} for n in range(8)]
    dup = build_request(
        DecisionPoint.DUPLICATE, {**stored_inputs("duplicate"), "candidates": candidates}
    )
    assert len(dup.state["candidates"]) == 5
    assert sorted(dup.questions) == [f"dup_{n}" for n in range(1, 6)]


@pytest.mark.req("FR-11.9")
@pytest.mark.wp("P1-01")
def test_state_plus_longest_question_is_bounded_under_32k() -> None:
    """A project match under the 60,000-token total but with state plus its question over
    30,000 is refused too (Jev's 32k bound per question)."""
    projects = [
        {"name": "N" * 120, "client": "C" * 120, "goal": "G" * 200, "keywords": ["k" * 60] * 4}
        for _ in range(254)
    ]
    with pytest.raises(DecisionRequestTooLarge) as refused:
        build_request(
            DecisionPoint.PROJECT_MATCH, {**stored_inputs("project_match"), "projects": projects}
        )
    assert refused.value.limit == MAX_STATE_PLUS_QUESTION_TOKENS
    assert MAX_STATE_PLUS_QUESTION_TOKENS < refused.value.estimated_tokens <= MAX_REQUEST_TOKENS


@pytest.mark.req("FR-11.2")
@pytest.mark.wp("P1-01")
def test_choice_needs_two_options_and_questions_are_fixed_data() -> None:
    """A Choice of one option is refused; duplicates need a candidate to ask about; fixed
    points ask the same questions whatever the inputs."""
    with pytest.raises(ValidationError):
        ChoiceDef(instructions="Pick", criteria={"only": "one"})
    with pytest.raises(MissingDecisionInput):
        questions_for(DecisionPoint.DUPLICATE, {})
    assert questions_for(DecisionPoint.ACTIONABILITY, {}) == questions_for(
        DecisionPoint.ACTIONABILITY, stored_inputs("actionability")
    )


@pytest.mark.req("FR-11.2")
@pytest.mark.wp("P1-01")
def test_option_keys_round_trip_and_models_must_be_pinned() -> None:
    """p01..p254 map back to their positions and nothing else does; only versioned model
    ids count as pinned."""
    for position in (1, 9, 10, 99, 100, 254):
        assert option_position(option_key(position)) == position
    assert option_key(1) == "p01"
    assert option_key(100) == "p100"
    for other in ("unknown", "p1", "p00", "p001", "p0100", "x01", ""):
        assert option_position(other) is None
    with pytest.raises(ValueError, match="start at 1"):
        option_key(0)
    assert is_pinned_model("jev-1.13.0")
    assert is_pinned_model("qwen3-8b-2.0.1")
    for alias in ("jev-latest", "jev", "jev-1.13", "Jev-1.13.0", "jev-1.13.0-rc"):
        assert not is_pinned_model(alias)
