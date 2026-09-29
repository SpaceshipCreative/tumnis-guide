"""The decisions fake's scripting (P1-01): what later WPs use to drive degraded paths."""

from __future__ import annotations

import pytest

from tumnis.core.adapters.errors import AdapterTimeout, AdapterUnavailable
from tumnis.modules.decisions.adapters.fake import FakeDecisions
from tumnis.modules.decisions.api import ask_raw
from tumnis.modules.decisions.catalog import DecisionPoint
from tumnis.modules.decisions.tests._cases import PINNED_MODEL, stored_inputs

LABEL = DecisionPoint.QUICK_ADD_LABEL


@pytest.mark.req("FR-11.1")
@pytest.mark.wp("P1-01")
async def test_fake_answers_as_scripted_and_records_calls() -> None:
    """A scripted answer (given as a dict) replaces the default for that question only;
    latency is slept and reported; every ask is recorded; reset clears it all."""
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    fake = FakeDecisions(sleep=sleep)
    fake.script(
        "quick_add_label",
        {
            "label": {
                "type": "choice",
                "choice": "hybrid",
                "confidence": 0.93,
                "probabilities": {"human": 0.04, "ai": 0.02, "hybrid": 0.93, "unknown": 0.01},
            }
        },
        latency_ms=250,
    )
    resp = await ask_raw(fake, LABEL, stored_inputs("quick_add_label"), model=PINNED_MODEL)
    label = resp.answers["label"]
    assert label.type == "choice"
    assert label.choice == "hybrid"
    assert resp.answers["needs_decision"].type == "noul"
    assert resp.latency_ms == 250
    assert slept == [0.25]
    assert [(c.point, c.model, c.timeout_ms) for c in fake.calls] == [(LABEL, PINNED_MODEL, 800)]

    fake.reset()
    assert fake.calls == []
    again = await ask_raw(fake, LABEL, stored_inputs("quick_add_label"), model=PINNED_MODEL)
    assert again.answers["label"].type == "choice"
    assert again.answers["label"].choice == "human"


@pytest.mark.req("FR-11.1")
@pytest.mark.wp("P1-01")
async def test_fake_fails_times_out_and_degrades_on_request() -> None:
    """A scripted failure is raised; a latency over the call's timeout raises
    AdapterTimeout after the timeout; health follows set_health."""
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    fake = FakeDecisions(sleep=sleep)
    fake.script(LABEL, fail=AdapterUnavailable("decisions.fake", "system_one", "down"))
    with pytest.raises(AdapterUnavailable):
        await ask_raw(fake, LABEL, stored_inputs("quick_add_label"), model=PINNED_MODEL)
    fake.script(LABEL, latency_ms=5_000)
    with pytest.raises(AdapterTimeout):
        await ask_raw(
            fake, LABEL, stored_inputs("quick_add_label"), model=PINNED_MODEL, timeout_ms=900
        )
    assert slept == [0.9]
    fake.set_health("degraded")
    assert fake.health_state() == "degraded"
    assert await fake.health() == "degraded"
