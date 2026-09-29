"""The decisions provider contract (P1-02, FR-11.3) over the vLLM fallback: `VllmDecisions`
replaying recorded OpenAI-compatible chat completions, and the fake registered as
`decisions.vllm`.

The replay transport answers each POST /v1/chat/completions with the recorded exchange
whose request body equals the one sent, apart from `model`: vLLM answers under the served
model name the request asked for, so the replay echoes it (as vLLM does).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from tumnis.modules.decisions.adapters.port import DecisionsProvider
from tumnis.modules.decisions.tests._cases import PINNED_MODEL, answered_recordings
from tumnis.modules.decisions.tests.contract.base import DecisionsProviderContract

if TYPE_CHECKING:
    from tests.fixtures import Fakes

pytestmark = pytest.mark.contract

VLLM_RECORDINGS = Path(__file__).resolve().parents[1] / "recordings" / "vllm"
BASE_URL = "http://10.20.0.5:8000"  # the homelab vLLM's address shape (private, port 8000)


def load_vllm_recordings() -> list[tuple[str, dict[str, Any]]]:
    """(file name, recording) for every tests/recordings/vllm/*.json. Each recording is
    {point, inputs, exchanges: [{request, response: {status, body}}], recorded_at, notes}."""
    return [
        (path.name, json.loads(path.read_text())) for path in sorted(VLLM_RECORDINGS.glob("*.json"))
    ]


def _key(body: dict[str, Any]) -> str:
    return json.dumps({k: v for k, v in body.items() if k != "model"}, sort_keys=True)


def replay_transport(
    recordings: list[dict[str, Any]], seen: list[dict[str, Any]] | None = None
) -> httpx.MockTransport:
    exchanges = {
        _key(exchange["request"]): exchange for rec in recordings for exchange in rec["exchanges"]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        if seen is not None:
            seen.append(body)
        exchange = exchanges.get(_key(body))
        assert exchange is not None, f"no recording for the request sent: {_key(body)[:400]}"
        response = exchange["response"]
        answer = (
            {**response["body"], "model": body["model"]}
            if response["status"] == 200
            else (response["body"])
        )
        return httpx.Response(response["status"], json=answer)

    return httpx.MockTransport(handler)


def _vllm(transport: httpx.MockTransport) -> Any:
    from tumnis.core.clock import FixedClock  # noqa: PLC0415
    from tumnis.core.net import NetPolicy  # noqa: PLC0415
    from tumnis.modules.decisions.adapters.vllm import VllmDecisions  # noqa: PLC0415

    return VllmDecisions(
        BASE_URL,
        clock=FixedClock(datetime(2026, 3, 9, 12, 0, tzinfo=UTC)),
        net_policy=NetPolicy(mode="self-hosted"),
        transport=transport,
    )


@pytest.mark.req("FR-11.3")
@pytest.mark.wp("P1-02")
class TestVllmFake(DecisionsProviderContract):
    """The fake registered as the fallback passes the same suite."""

    impl = "fake"
    adapter_name = "decisions.vllm"

    @pytest.fixture
    def subject(self, fakes: Fakes) -> DecisionsProvider:
        provider: DecisionsProvider = fakes["decisions.vllm"]
        return provider


@pytest.mark.req("FR-11.3")
@pytest.mark.wp("P1-02")
class TestVllmOnRecordings(DecisionsProviderContract):
    """T-P1-02-15"""

    impl = "recorded"
    adapter_name = "decisions.vllm"

    @pytest.fixture
    def subject(self) -> DecisionsProvider:
        recordings = [rec for _, rec in load_vllm_recordings()]
        provider: DecisionsProvider = _vllm(replay_transport(recordings))
        return provider


@pytest.mark.req("FR-11.3")
@pytest.mark.wp("P1-02")
async def test_request_bodies_use_structured_outputs() -> None:
    """T-P1-02-15
    Every recorded case sends one chat completion per question, sampled `n = 5` times at
    temperature 0.7, constrained with vLLM's current `structured_outputs` field (never the
    `guided_*` fields removed in v0.12.0): a Choice's option keys, `yes`/`no` for a Noul,
    and the level indices for a Score. The model asked for is the one passed.
    """
    from tumnis.modules.decisions.catalog import (  # noqa: PLC0415
        CATALOGUE,
        DecisionPoint,
        build_request,
    )

    recordings = load_vllm_recordings()
    assert {rec["point"] for _, rec in recordings} == {p.value for p in DecisionPoint}
    seen: list[dict[str, Any]] = []
    subject = _vllm(replay_transport([rec for _, rec in recordings], seen))
    for _, rec in answered_recordings():
        req = build_request(DecisionPoint(rec["point"]), rec["inputs"])
        seen.clear()
        await subject.ask(req, model=PINNED_MODEL, timeout_ms=CATALOGUE[req.point].timeout_ms)
        assert len(seen) == len(req.questions)
        allowed = []
        for question in req.questions.values():
            if question.type == "choice":
                allowed.append(sorted(question.criteria))
            elif question.type == "score":
                allowed.append(sorted(str(n) for n in range(len(question.criteria))))
            else:
                allowed.append(["no", "yes"])
        assert sorted(sorted(body["structured_outputs"]["choice"]) for body in seen) == sorted(
            allowed
        )
        for body in seen:
            assert body["model"] == PINNED_MODEL
            assert body["n"] == 5
            assert body["temperature"] == pytest.approx(0.7)
            assert not any(key.startswith("guided_") for key in body)
