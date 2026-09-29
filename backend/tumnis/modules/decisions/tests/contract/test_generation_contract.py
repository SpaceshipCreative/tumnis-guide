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
class TestFakeGeneration(GenerationProviderContract):
    """T-P1-03-05"""

    impl = "fake"

    @pytest.fixture
    def subject(self, fakes: Fakes) -> GenerationProvider:
        provider: GenerationProvider = fakes["decisions.vllm_generation"]
        return provider


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
class TestVllmGenerationOnRecordings(GenerationProviderContract):
    """T-P1-03-05"""

    impl = "recorded"

    @pytest.fixture
    def subject(self) -> GenerationProvider:
        provider: GenerationProvider = vllm_generation(
            replay_transport([rec for _, rec in load_generation_recordings()])
        )
        return provider


def _recording(name: str) -> Recording:
    return dict(load_generation_recordings())[name]


def _answer(status: int, body: Any = None, headers: dict[str, str] | None = None) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body, headers=headers or {})

    return vllm_generation(httpx.MockTransport(handler))


async def _ask(subject: Any, rec: Recording | None = None) -> str:
    rec = rec or _recording("placeholder__acme_agreement.json")
    text: str = await subject.complete(
        system=rec["input"]["system"],
        user=rec["input"]["user"],
        max_tokens=rec["input"]["max_tokens"],
        timeout_ms=2000,
    )
    return text


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
async def test_recorded_request_and_answer_round_trip() -> None:
    """The body sent is the recording's request (the served model, the system and user
    messages, max_tokens, a low temperature, one choice, no streaming) to the endpoint's
    /v1/chat/completions on its own port, pinned to the resolved LAN address; the answer
    is the first choice's content, unchanged (the cleanup is `generation_api`'s). The log
    line carries the model, token counts and latency, never the prompt or the answer."""
    from structlog.testing import capture_logs  # noqa: PLC0415

    rec = _recording("placeholder__two_lines.json")
    seen: list[dict[str, Any]] = []
    urls: list[str] = []
    inner = replay_transport([rec], seen)

    async def spy(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return await inner.handle_async_request(request)

    subject = vllm_generation(httpx.MockTransport(spy))
    with capture_logs() as logs:
        text = await _ask(subject, rec)

    assert seen == [rec["request"]["body"]]
    assert urls == [f"http://{LAN_ADDRESS}:8000/v1/chat/completions"]
    assert text == rec["response"]["body"]["choices"][0]["message"]["content"]
    (line,) = [log for log in logs if log["event"] == "decisions.generation_call"]
    assert line["model"] == GENERATION_MODEL
    assert line["prompt_tokens"] == rec["response"]["body"]["usage"]["prompt_tokens"]
    assert "latency_ms" in line
    assert "Broken" not in repr(line)
    assert "staging" not in repr(line)


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
@pytest.mark.parametrize("base_url", ["http://vllm.example.org:8000/", GENERATION_BASE_URL + "/v1"])
async def test_base_url_may_name_the_api_root(base_url: str) -> None:
    """A trailing slash or the /v1 API root in the configured URL reach the same path."""
    from datetime import UTC, datetime  # noqa: PLC0415

    from tumnis.core.clock import FixedClock  # noqa: PLC0415
    from tumnis.core.net import NetPolicy, ScriptedResolver  # noqa: PLC0415
    from tumnis.modules.decisions.adapters.vllm_generation import (  # noqa: PLC0415
        VllmGeneration,
    )

    subject = VllmGeneration(
        base_url,
        GENERATION_MODEL,
        clock=FixedClock(datetime(2026, 3, 9, 12, 0, tzinfo=UTC)),
        net_policy=NetPolicy(mode="self-hosted"),
        resolver=ScriptedResolver([[LAN_ADDRESS]]),
        transport=replay_transport([_recording("placeholder__acme_agreement.json")]),
    )
    assert await _ask(subject)


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
async def test_errors_map_to_the_adapter_errors() -> None:
    """The recorded 503 and a 429 are retryable AdapterUnavailable (with Retry-After);
    another 4xx, a body without message content or not JSON are AdapterRejected; a read
    timeout is AdapterTimeout and a refused connection AdapterUnavailable. Each call sends
    once."""
    from tumnis.core.adapters.errors import (  # noqa: PLC0415
        AdapterRejected,
        AdapterTimeout,
        AdapterUnavailable,
    )

    overloaded = _recording("error__server_overloaded.json")
    with pytest.raises(AdapterUnavailable) as busy:
        await _ask(vllm_generation(replay_transport([overloaded])), overloaded)
    assert busy.value.retryable

    with pytest.raises(AdapterUnavailable) as limited:
        await _ask(_answer(429, {"object": "error"}, {"Retry-After": "2"}))
    assert limited.value.retry_after_s == pytest.approx(2.0)

    for rejected in (
        _answer(400, {"object": "error", "message": "max_tokens too large"}),
        _answer(200, {"object": "chat.completion", "choices": []}),
        _answer(200, ["not", "an", "object"]),
    ):
        with pytest.raises(AdapterRejected):
            await _ask(rejected)

    def not_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>proxy error</html>")

    with pytest.raises(AdapterRejected):
        await _ask(vllm_generation(httpx.MockTransport(not_json)))

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    with pytest.raises(AdapterTimeout):
        await _ask(vllm_generation(httpx.MockTransport(slow)))

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(AdapterUnavailable):
        await _ask(vllm_generation(httpx.MockTransport(refused)))


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
@pytest.mark.parametrize("usage", [["not", "a", "dict"], "12 tokens", 7])
async def test_malformed_usage_keeps_the_answer(usage: Any) -> None:
    """Usage metadata that is not an object only drops the token counts from the log line;
    the generated text still comes back (PR #58 review)."""
    body = {"choices": [{"message": {"role": "assistant", "content": "Open the file."}}]}
    assert await _ask(_answer(200, {**body, "usage": usage})) == "Open the file."


@pytest.mark.req("FR-11.8", "SEC-5")
@pytest.mark.wp("P1-03")
async def test_blocked_destination_sends_nothing() -> None:
    """The endpoint goes through the SSRF guard: a name resolving to the cloud metadata
    address is refused before any request."""
    from datetime import UTC, datetime  # noqa: PLC0415

    from tumnis.core.clock import FixedClock  # noqa: PLC0415
    from tumnis.core.net import NetPolicy, ScriptedResolver, SsrfBlocked  # noqa: PLC0415
    from tumnis.modules.decisions.adapters.vllm_generation import (  # noqa: PLC0415
        VllmGeneration,
    )

    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    subject = VllmGeneration(
        GENERATION_BASE_URL,
        GENERATION_MODEL,
        clock=FixedClock(datetime(2026, 3, 9, 12, 0, tzinfo=UTC)),
        net_policy=NetPolicy(mode="self-hosted"),
        resolver=ScriptedResolver([["169.254.169.254"]]),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(SsrfBlocked):
        await _ask(subject)
    assert sent == []
