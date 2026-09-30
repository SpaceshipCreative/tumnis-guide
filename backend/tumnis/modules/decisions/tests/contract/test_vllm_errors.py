"""How VllmDecisions maps HTTP failures onto the adapter errors (P1-02, P0-09), and that it
only reaches an address the deployment mode allows."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from tumnis.core.adapters.errors import AdapterRejected, AdapterTimeout, AdapterUnavailable
from tumnis.core.clock import FixedClock
from tumnis.core.net import NetPolicy, SsrfBlocked
from tumnis.modules.decisions.adapters import build_vllm
from tumnis.modules.decisions.adapters.vllm import VllmDecisions
from tumnis.modules.decisions.catalog import DecisionPoint, build_request
from tumnis.modules.decisions.tests._cases import stored_inputs

pytestmark = [pytest.mark.contract, pytest.mark.req("FR-11.3"), pytest.mark.wp("P1-02")]
CLOCK = FixedClock(datetime(2026, 3, 9, 12, 0, tzinfo=UTC))
MODEL = "served-model"
Handler = Callable[[httpx.Request], httpx.Response]


def _vllm(handler: Handler, *, base_url: str = "http://10.20.0.5:8000") -> VllmDecisions:
    return VllmDecisions(
        base_url,
        clock=CLOCK,
        net_policy=NetPolicy(mode="self-hosted"),
        transport=httpx.MockTransport(handler),
    )


def _raise(exc: Exception) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


def _status(status: int, headers: dict[str, str] | None = None) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "no"}, headers=headers)

    return handler


def _ok(body: Any) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    return handler


def _choices(*contents: str) -> dict[str, Any]:
    return {
        "model": MODEL,
        "choices": [{"index": i, "message": {"content": c}} for i, c in enumerate(contents)],
        "usage": {"prompt_tokens": 7},
    }


async def _ask(vllm: VllmDecisions) -> Any:
    req = build_request(DecisionPoint.ACTIONABILITY, stored_inputs("actionability"))
    return await vllm.ask(req, model=MODEL, timeout_ms=2_000)


@pytest.mark.parametrize(
    ("handler", "error", "retryable"),
    [
        (_status(500), AdapterUnavailable, True),
        (_status(503), AdapterUnavailable, True),
        (_status(408), AdapterUnavailable, True),
        (_status(429), AdapterUnavailable, True),
        (_status(400), AdapterRejected, False),
        (_status(404), AdapterRejected, False),
        (_raise(httpx.ConnectError("refused")), AdapterUnavailable, True),
        (_raise(httpx.ReadTimeout("slow")), AdapterTimeout, True),
        (_ok({"nothing": "here"}), AdapterRejected, False),
        (_ok(_choices("maybe", "perhaps")), AdapterRejected, False),
    ],
)
async def test_http_failures_become_adapter_errors(
    handler: Handler, error: type[Exception], retryable: bool
) -> None:
    """5xx, 408, 429 and connection failures are retryable AdapterUnavailable, a timeout
    is AdapterTimeout, any other 4xx and an answer that is not one of the allowed ones is
    AdapterRejected."""
    vllm = _vllm(handler)
    with pytest.raises(error) as failed:
        await _ask(vllm)
    assert failed.value.retryable is retryable  # type: ignore[attr-defined]
    await vllm.aclose()


async def test_retry_after_is_passed_on() -> None:
    vllm = _vllm(_status(429, {"retry-after": "7"}))
    with pytest.raises(AdapterUnavailable) as failed:
        await _ask(vllm)
    assert failed.value.retry_after_s == 7.0
    await vllm.aclose()


async def test_a_body_that_is_not_json_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>proxy</html>")

    vllm = _vllm(handler)
    with pytest.raises(AdapterRejected):
        await _ask(vllm)
    await vllm.aclose()


async def test_the_answer_reports_the_served_model_and_prompt_tokens() -> None:
    vllm = _vllm(_ok(_choices("yes", "yes", "no", "yes", "yes")))
    resp = await _ask(vllm)
    assert resp.provider == "vllm"
    assert resp.model == MODEL
    assert resp.input_tokens == 7
    assert resp.answers["actionable"].noul == pytest.approx(0.8)
    assert await vllm.health() == "ok"
    await vllm.aclose()


async def test_the_configured_port_is_reachable_but_nothing_else_private_in_hosted_mode() -> None:
    """A vLLM on a private address is allowed in self-hosted mode on its own port; in hosted
    mode a private address needs the allow-list, and nothing is sent."""
    hosted = VllmDecisions(
        "http://10.20.0.5:8000",
        clock=CLOCK,
        net_policy=NetPolicy(mode="hosted"),
        transport=httpx.MockTransport(_ok(_choices("yes"))),
    )
    with pytest.raises(SsrfBlocked):
        await _ask(hosted)
    await hosted.aclose()
    other_port = _vllm(_ok(_choices("yes")), base_url="http://10.20.0.5:9999")
    assert (await _ask(other_port)).answers["actionable"].noul == 1.0
    await other_port.aclose()


def test_the_registry_builds_vllm_lazily() -> None:
    built = build_vllm(
        base_url="http://10.20.0.5:8000", clock=CLOCK, net_policy=NetPolicy(mode="self-hosted")
    )
    assert isinstance(built, VllmDecisions)
    assert built.provider == "vllm"
