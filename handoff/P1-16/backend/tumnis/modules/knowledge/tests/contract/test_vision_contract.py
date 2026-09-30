"""The vision model's contract (P1-16, ADR-0007): a page image in, the page's Markdown out.
`VllmVision` replays recorded OpenAI-compatible chat completions through the SSRF-guarded
client (a scripted resolver and an `httpx.MockTransport`: no socket is opened);
`FakeVision` answers per-page Markdown and passes the same cases."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from tumnis.core.adapters.contract import AdapterContract
from tumnis.modules.knowledge.adapters.port import Vision

if TYPE_CHECKING:
    from tests.fixtures import Fakes

pytestmark = pytest.mark.contract

RECORDINGS = Path(__file__).resolve().parents[1] / "recordings" / "vllm_vision"
BASE_URL = "http://vllm.example.org:8000"
MODEL = "ibm-granite/granite-docling-258M"
LAN_ADDRESS = "192.168.20.40"  # the homelab vLLM's (invented) LAN address
Recording = dict[str, Any]


def load_recordings() -> list[tuple[str, Recording]]:
    return [(p.stem, json.loads(p.read_text())) for p in sorted(RECORDINGS.glob("*.json"))]


def _canonical(body: Any) -> str:
    return json.dumps(body, sort_keys=True, ensure_ascii=False)


def replay_transport(recordings: list[Recording]) -> httpx.MockTransport:
    """Answers each POST /v1/chat/completions with the recording whose request body equals
    the body sent; any other request fails the test."""
    by_body = {_canonical(rec["request"]["body"]): rec for rec in recordings}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/chat/completions"
        rec = by_body.get(_canonical(json.loads(request.content)))
        assert rec is not None, "no recording for the request sent"
        response = rec["response"]
        return httpx.Response(
            response["status"], json=response["body"], headers=response.get("headers", {})
        )

    return httpx.MockTransport(handler)


def page_cases() -> list[tuple[str, bytes, int]]:
    """(name, page image, page number) for every recording that answered 200."""
    return [
        (name, base64.b64decode(rec["input"]["image_png_base64"]), rec["input"]["page"])
        for name, rec in load_recordings()
        if rec["response"]["status"] == 200
    ]


class VisionContract(AdapterContract[Vision]):
    port, adapter_name = Vision, "knowledge.vision"

    async def test_page_in_markdown_out(self, subject: Vision) -> None:
        cases = page_cases()
        assert cases, "no recorded vision pages"
        for name, image, page in cases:
            markdown = await subject.page_markdown(image, page=page)
            assert isinstance(markdown, str), name
            assert markdown.strip(), name

    async def test_health_is_ok_when_fresh(self, subject: Vision) -> None:
        assert await subject.health() == "ok"


@pytest.mark.req("ADR-0007")
@pytest.mark.wp("P1-16")
@pytest.mark.xfail(strict=True, reason="spec:P1-16")
class TestFakeVision(VisionContract):
    """T-P1-16-13
    The vision fake answers Markdown for every page it is shown.
    """

    impl = "fake"

    @pytest.fixture
    def subject(self, fakes: Fakes) -> Vision:
        vision: Vision = fakes["knowledge.vision"]
        return vision


@pytest.mark.req("ADR-0007")
@pytest.mark.wp("P1-16")
@pytest.mark.xfail(strict=True, reason="spec:P1-16")
class TestVllmVision(VisionContract):
    """T-P1-16-13
    `VllmVision` on recorded vLLM answers: each recorded page image comes back as
    Markdown, sent as one chat completion with the image inline.
    """

    impl = "recorded"

    @pytest.fixture
    def subject(self) -> Vision:
        from datetime import UTC, datetime  # noqa: PLC0415

        from tumnis.core.clock import FixedClock  # noqa: PLC0415
        from tumnis.core.net import NetPolicy, ScriptedResolver  # noqa: PLC0415
        from tumnis.modules.knowledge.adapters.vision import VllmVision  # noqa: PLC0415

        recordings = [rec for _, rec in load_recordings()]
        return VllmVision(
            BASE_URL,
            MODEL,
            clock=FixedClock(datetime(2026, 3, 9, 12, 0, tzinfo=UTC)),
            net_policy=NetPolicy(mode="self-hosted"),
            resolver=ScriptedResolver([[LAN_ADDRESS]] * max(1, len(recordings))),
            transport=replay_transport(recordings),
        )
