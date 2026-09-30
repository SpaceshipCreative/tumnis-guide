"""The decisions fakes' stored scripts (R-37, `POST /v1/test/fakes/{adapter}/script`): what
the route accepts for `decisions.jev`, `decisions.vllm` and `generation`, and how a stored
script becomes the fake's answer."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from tumnis.core import fake_scripts
from tumnis.modules.decisions.adapters.fake import (
    FakeDecisions,
    FakeGeneration,
    parse_decisions_script,
    parse_generation_script,
    scripted,
)
from tumnis.modules.decisions.catalog import DecisionPoint, build_request
from tumnis.modules.decisions.tests._cases import stored_inputs

JEV_SCRIPT = {"question": "quick_add_label", "answer": "hybrid", "confidence": 0.93}


def _questions(point: DecisionPoint) -> dict[str, Any]:
    return dict(build_request(point, stored_inputs(point.value)).questions)


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
def test_decisions_script_is_keyed_by_its_decision_point() -> None:
    """T-P0-04-20
    The journeys' body names the decision point as `question`; it is the match key, and
    the body is stored with its defaults filled in.
    """
    key, stored = parse_decisions_script({**JEV_SCRIPT, "latency_ms": 250})
    assert key == "quick_add_label"
    assert stored["answer"] == "hybrid"
    assert stored["latency_ms"] == 250
    assert parse_decisions_script({"question": "actionability"})[1]["latency_ms"] == 0


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
@pytest.mark.parametrize(
    "body",
    [
        {"question": "not_a_point"},
        {**JEV_SCRIPT, "confidence": 0},
        {**JEV_SCRIPT, "confidence": 1.01},
        {**JEV_SCRIPT, "latency_ms": -5},
        {**JEV_SCRIPT, "extra": 1},
        {"answer": "hybrid"},
    ],
)
def test_decisions_script_refuses_what_the_fake_cannot_read(body: dict[str, Any]) -> None:
    """T-P0-04-21
    An unknown point, a confidence outside (0, 1], a negative latency, an unknown key or
    no point at all is refused when posted, not when the fake is asked.
    """
    with pytest.raises(ValidationError):
        parse_decisions_script(body)


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
def test_choice_shorthand_leads_with_its_confidence() -> None:
    """T-P0-04-22
    `answer` and `confidence` script the point's main question: the answer wins at the
    confidence and the other options share the rest; `latency_ms` carries over.
    """
    _, stored = parse_decisions_script({**JEV_SCRIPT, "latency_ms": 250})
    script = scripted(stored, _questions(DecisionPoint.QUICK_ADD_LABEL))
    label = script.answers["label"]
    assert label.type == "choice"
    assert label.choice == "hybrid"
    assert label.confidence == pytest.approx(0.93)
    assert label.probabilities["hybrid"] == pytest.approx(0.93)
    assert sum(label.probabilities.values()) == pytest.approx(1.0)
    assert script.latency_ms == 250


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
def test_score_and_noul_shorthands() -> None:
    """T-P0-04-23
    A Score's `answer` is a level index (the expected level follows the spread); a Noul's
    is the probability of yes.
    """
    _, stored = parse_decisions_script(
        {"question": "blocking_impact", "answer": "1", "confidence": 0.8}
    )
    score = scripted(stored, _questions(DecisionPoint.BLOCKING_IMPACT)).answers["impact"]
    assert score.type == "score"
    assert score.probabilities["1"] == pytest.approx(0.8)
    assert sum(score.probabilities.values()) == pytest.approx(1.0)
    expected = sum(int(level) * p for level, p in score.probabilities.items())
    assert score.score == pytest.approx(expected)

    _, stored = parse_decisions_script({"question": "actionability", "answer": 0.88})
    noul = scripted(stored, _questions(DecisionPoint.ACTIONABILITY)).answers["actionable"]
    assert noul.type == "noul"
    assert noul.noul == pytest.approx(0.88)


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
def test_an_answer_that_is_not_an_option_fails_loudly() -> None:
    """T-P0-04-24
    A shorthand answer the asked question does not offer raises when the fake is asked,
    instead of a default answer letting a mis-scripted journey pass.
    """
    _, stored = parse_decisions_script({**JEV_SCRIPT, "answer": "robot"})
    with pytest.raises(ValueError, match="robot"):
        scripted(stored, _questions(DecisionPoint.QUICK_ADD_LABEL))


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
def test_generation_script_sets_the_first_action() -> None:
    """T-P0-04-25
    `generation` takes `{first_action, delay_ms}` under one key; an empty first action or an
    unknown key is refused.
    """
    key, stored = parse_generation_script({"first_action": "Open the invoice template"})
    assert key == ""
    assert stored == {"first_action": "Open the invoice template", "delay_ms": 0}
    for bad in ({}, {"first_action": ""}, {"first_action": "x", "text": "y"}):
        with pytest.raises(ValidationError):
            parse_generation_script(bad)


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
async def test_fakes_answer_stored_scripts_while_the_store_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P0-04-26
    With the store enabled, `FakeDecisions` answers the script stored under its hook and
    the asked point, and `FakeGeneration` the one under `generation`; with it disabled
    (unit tests, any process that did not enable it) they keep their in-memory scripts.
    """
    rows = {
        ("decisions.jev", "quick_add_label"): parse_decisions_script(JEV_SCRIPT)[1],
        ("generation", ""): parse_generation_script({"first_action": "From the store"})[1],
    }

    async def lookup(adapter: str, key: str = "") -> dict[str, Any] | None:
        if not fake_scripts.enabled():
            return None
        return rows.get((adapter, key))

    monkeypatch.setattr(fake_scripts, "lookup", lookup)
    req = build_request(DecisionPoint.QUICK_ADD_LABEL, stored_inputs("quick_add_label"))
    jev, vllm, generation = FakeDecisions(), FakeDecisions(hook="decisions.vllm"), FakeGeneration()

    before = await jev.ask(req, model="m", timeout_ms=800)
    assert before.answers["label"].type == "choice"
    assert before.answers["label"].choice == "human"
    assert await generation.complete(system="s", user="u", max_tokens=5, timeout_ms=100) == (
        FakeGeneration.DEFAULT_TEXT
    )

    monkeypatch.setattr(fake_scripts, "_enabled", True)
    after = await jev.ask(req, model="m", timeout_ms=800)
    assert after.answers["label"].type == "choice"
    assert after.answers["label"].choice == "hybrid"
    fallback = await vllm.ask(req, model="m", timeout_ms=800)
    assert fallback.answers["label"].type == "choice"
    assert fallback.answers["label"].choice == "human"
    assert await generation.complete(system="s", user="u", max_tokens=5, timeout_ms=100) == (
        "From the store"
    )
