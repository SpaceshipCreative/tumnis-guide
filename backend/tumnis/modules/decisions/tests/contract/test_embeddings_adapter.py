"""The Embeddings slot's shared contract (P3-10, FR-11.1, FR-11.10): the fake and
`VllmEmbeddings` replaying recorded OpenAI-compatible `/v1/embeddings` exchanges answer
alike. Every vector has the adapter's `dims`, a batch comes back in the order it was
sent (whatever order the server lists its `data` in), and an empty batch is answered
with an empty list without a call.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.contract

RECORDINGS = Path(__file__).resolve().parents[1] / "recordings" / "vllm_embeddings"
BASE_URL = "http://10.20.0.5:8000"  # the homelab vLLM's address shape (private, port 8000)
TEXTS = (
    "Passwords are hashed with Argon2id.",
    "Invoices are due within thirty days.",
    "The newsletter goes out on the second Tuesday of every month.",
)


def load_recordings() -> list[dict[str, Any]]:
    """Every tests/recordings/vllm_embeddings/*.json: {model, dims, exchanges: [{request,
    response: {status, body}}], recorded_at, notes}."""
    return [json.loads(path.read_text()) for path in sorted(RECORDINGS.glob("*.json"))]


def _key(body: dict[str, Any]) -> str:
    return json.dumps(body, sort_keys=True)


def replay_transport(
    recordings: list[dict[str, Any]], seen: list[dict[str, Any]]
) -> httpx.MockTransport:
    """Answers POST /v1/embeddings with the recorded exchange whose request body equals the
    one sent; any other request fails the test."""
    exchanges = {
        _key(exchange["request"]): exchange for rec in recordings for exchange in rec["exchanges"]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/embeddings"
        body = json.loads(request.content)
        seen.append(body)
        exchange = exchanges.get(_key(body))
        assert exchange is not None, f"no recording for the request sent: {_key(body)[:300]}"
        return httpx.Response(exchange["response"]["status"], json=exchange["response"]["body"])

    return httpx.MockTransport(handler)


async def _shared_cases(subject: Any, sent: list[dict[str, Any]] | None = None) -> None:
    vectors = await subject.embed(list(TEXTS))
    assert len(vectors) == len(TEXTS)
    assert all(len(vector) == subject.dims for vector in vectors)
    assert all(isinstance(x, float) for vector in vectors for x in vector)

    reordered = await subject.embed([TEXTS[2], TEXTS[0]])
    assert reordered == [vectors[2], vectors[0]]

    calls = None if sent is None else len(sent)
    assert await subject.embed([]) == []
    if sent is not None:
        assert len(sent) == calls  # nothing was sent for the empty batch


@pytest.mark.req("FR-11.1", "FR-11.10")
@pytest.mark.wp("P3-10")
async def test_contract_fake_and_recorded_vllm() -> None:
    """T-P3-10-11
    The same cases pass against the fake and against VllmEmbeddings replaying the recorded
    vLLM answers: dims match, batch order kept, empty input returns empty with no call.
    """
    from tumnis.core.clock import FixedClock  # noqa: PLC0415
    from tumnis.core.net import NetPolicy  # noqa: PLC0415
    from tumnis.modules.decisions.adapters.embeddings.fake import (  # noqa: PLC0415
        FakeEmbeddings,
    )
    from tumnis.modules.decisions.adapters.embeddings.vllm import (  # noqa: PLC0415
        VllmEmbeddings,
    )

    await _shared_cases(FakeEmbeddings())

    recordings = load_recordings()
    assert recordings, "no recorded vLLM embeddings"
    seen: list[dict[str, Any]] = []
    recorded = VllmEmbeddings(
        BASE_URL,
        recordings[0]["model"],
        recordings[0]["dims"],
        clock=FixedClock(datetime(2026, 3, 9, 12, 0, tzinfo=UTC)),
        net_policy=NetPolicy(mode="self-hosted"),
        transport=replay_transport(recordings, seen),
    )
    assert recorded.hosted is False
    await _shared_cases(recorded, seen)
    assert [body["input"] for body in seen] == [list(TEXTS), [TEXTS[2], TEXTS[0]]]
