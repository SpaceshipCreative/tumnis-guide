"""The Generation slot's contract (P1-03, FR-11.8), run against the fake and against
`VllmGeneration` replaying recorded OpenAI-compatible answers through the SSRF-guarded
client (a scripted resolver and an `httpx.MockTransport`: no socket is opened)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from tumnis.core.adapters.contract import AdapterContract
from tumnis.modules.decisions.adapters.port import GenerationProvider
from tumnis.modules.decisions.tests._cases import (
    GENERATION_BASE_URL,
    GENERATION_MODEL,
    load_generation_recordings,
)

if TYPE_CHECKING:
    from tests.fixtures import Fakes

pytestmark = pytest.mark.contract

Recording = dict[str, Any]
LAN_ADDRESS = "192.168.20.40"  # the homelab vLLM's (invented) LAN address


def _canonical(body: Any) -> str:
    return json.dumps(body, sort_keys=True, ensure_ascii=False)


def replay_transport(
    recordings: list[Recording], seen: list[dict[str, Any]] | None = None
) -> httpx.MockTransport:
    """Answers each POST /v1/chat/completions with the recording whose request body equals
    the body sent; any other request fails the test. Every body sent goes to `seen`."""
    by_body = {_canonical(rec["request"]["body"]): rec for rec in recordings}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        if seen is not None:
            seen.append(body)
        rec = by_body.get(_canonical(body))
        assert rec is not None, f"no recording for the request sent: {_canonical(body)[:400]}"
        response = rec["response"]
        return httpx.Response(
            response["status"], json=response["body"], headers=response.get("headers", {})
        )

    return httpx.MockTransport(handler)


def vllm_generation(transport: httpx.AsyncBaseTransport) -> Any:
    """`VllmGeneration` on the recordings' endpoint, resolving it to a LAN address."""
    from datetime import UTC, datetime  # noqa: PLC0415

    from tumnis.core.clock import FixedClock  # noqa: PLC0415
    from tumnis.core.net import NetPolicy, ScriptedResolver  # noqa: PLC0415
    from tumnis.modules.decisions.adapters.vllm_generation import (  # noqa: PLC0415
        VllmGeneration,
    )

    return VllmGeneration(
        GENERATION_BASE_URL,
        GENERATION_MODEL,
        clock=FixedClock(datetime(2026, 3, 9, 12, 0, tzinfo=UTC)),
        net_policy=NetPolicy(mode="self-hosted"),
        resolver=ScriptedResolver([[LAN_ADDRESS]]),
        transport=transport,
    )


def answered() -> list[tuple[str, Recording]]:
    return [(n, rec) for n, rec in load_generation_recordings() if rec["response"]["status"] == 200]


class GenerationProviderContract(AdapterContract[GenerationProvider]):
    port, adapter_name = GenerationProvider, "decisions.vllm_generation"

    async def test_completes_every_recorded_prompt_with_text(
        self, subject: GenerationProvider
    ) -> None:
        """T-P1-03-05
        For every recorded prompt (placeholder and spoken), `complete` returns non-empty
        text within the call's timeout.
        """
        cases = answered()
        assert {name.split("__")[0] for name, _ in cases} >= {"placeholder", "spoken"}
        for name, rec in cases:
            text = await subject.complete(
                system=rec["input"]["system"],
                user=rec["input"]["user"],
                max_tokens=rec["input"]["max_tokens"],
                timeout_ms=2000,
            )
            assert isinstance(text, str), name
            assert text.strip(), name

    async def test_health_is_ok_when_fresh(self, subject: GenerationProvider) -> None:
        """T-P1-03-05
        A provider that has not failed reports "ok".
        """
        assert await subject.health() == "ok"


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
@pytest.mark.xfail(strict=True, reason="spec:P1-03")
class TestFakeGeneration(GenerationProviderContract):
    """T-P1-03-05"""

    impl = "fake"

    @pytest.fixture
    def subject(self, fakes: Fakes) -> GenerationProvider:
        provider: GenerationProvider = fakes["decisions.vllm_generation"]
        return provider


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
@pytest.mark.xfail(strict=True, reason="spec:P1-03")
class TestVllmGenerationOnRecordings(GenerationProviderContract):
    """T-P1-03-05"""

    impl = "recorded"

    @pytest.fixture
    def subject(self) -> GenerationProvider:
        provider: GenerationProvider = vllm_generation(
            replay_transport([rec for _, rec in load_generation_recordings()])
        )
        return provider
