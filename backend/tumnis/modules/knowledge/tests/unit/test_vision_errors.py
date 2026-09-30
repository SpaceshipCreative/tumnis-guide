"""`VllmVision` answers (P1-16, ADR-0007): the adapter errors a vLLM failure becomes, and
the Markdown a fenced reply unwraps to. No socket is opened: a scripted resolver and an
`httpx.MockTransport` stand in for the server."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from tumnis.core.adapters.base import AdapterRejected, AdapterUnavailable
from tumnis.core.clock import FixedClock
from tumnis.core.net import NetPolicy, ScriptedResolver, SsrfBlocked
from tumnis.modules.knowledge.adapters.vision import VllmVision

pytestmark = [pytest.mark.req("ADR-0007"), pytest.mark.wp("P1-16")]

BASE_URL = "http://vllm.example.org:8000"
MODEL = "vision-model"


def _vision(handler: Any, *, address: str = "192.168.20.40") -> VllmVision:
    return VllmVision(
        BASE_URL,
        MODEL,
        clock=FixedClock(datetime(2026, 3, 9, 12, 0, tzinfo=UTC)),
        net_policy=NetPolicy(mode="self-hosted"),
        resolver=ScriptedResolver([[address]]),
        transport=httpx.MockTransport(handler),
    )


def _reply(content: Any) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


async def test_fenced_reply_is_unwrapped() -> None:
    vision = _vision(lambda _request: _reply("```markdown\n# Title\n\nBody\n```\n"))

    assert await vision.page_markdown(b"png", page=3) == "# Title\n\nBody"


@pytest.mark.parametrize("status", [429, 500, 503, 408])
async def test_server_trouble_is_unavailable(status: int) -> None:
    vision = _vision(lambda _request: httpx.Response(status, json={"error": "busy"}))

    with pytest.raises(AdapterUnavailable):
        await vision.page_markdown(b"png", page=1)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={"error": "bad image"}),
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json={"choices": []}),
        _reply(None),
    ],
)
async def test_refusals_and_malformed_answers_are_rejected(response: httpx.Response) -> None:
    vision = _vision(lambda _request: response)

    with pytest.raises(AdapterRejected):
        await vision.page_markdown(b"png", page=1)


async def test_blocked_address_sends_nothing() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return _reply("never")

    vision = _vision(handler, address="169.254.169.254")

    with pytest.raises(SsrfBlocked):
        await vision.page_markdown(b"png", page=1)
    assert sent == []
