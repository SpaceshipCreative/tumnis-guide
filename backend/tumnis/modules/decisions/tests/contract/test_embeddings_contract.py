"""The Embeddings slot's adapter contract classes (P3-10, FR-11.10): one case set run against
each fake and against `VllmEmbeddings` / `HostedEmbeddings` replaying the recorded
OpenAI-compatible `/v1/embeddings` answers (no socket is opened). The spec test
T-P3-10-11 lives in `test_embeddings_adapter.py`; these classes register the
implementations with the adapter registry's contract sweep (T-P0-09-13)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from tumnis.core.adapters.contract import AdapterContract
from tumnis.core.adapters.errors import AdapterRejected
from tumnis.modules.decisions.adapters.embeddings.port import EmbeddingsAdapter
from tumnis.modules.decisions.tests.contract.test_embeddings_adapter import (
    BASE_URL,
    TEXTS,
    load_recordings,
    replay_transport,
)

pytestmark = pytest.mark.contract


def _recorded(cls_name: str, transport: httpx.AsyncBaseTransport, **extra: Any) -> Any:
    from tumnis.core.clock import FixedClock  # noqa: PLC0415
    from tumnis.core.net import NetPolicy  # noqa: PLC0415
    from tumnis.modules.decisions.adapters.embeddings import hosted, vllm  # noqa: PLC0415

    cls = {"vllm": vllm.VllmEmbeddings, "hosted": hosted.HostedEmbeddings}[cls_name]
    rec = load_recordings()[0]
    return cls(
        BASE_URL,
        rec["model"],
        rec["dims"],
        clock=FixedClock(datetime(2026, 3, 9, 12, 0, tzinfo=UTC)),
        net_policy=NetPolicy(mode="self-hosted"),
        transport=transport,
        **extra,
    )


class EmbeddingsContract(AdapterContract[EmbeddingsAdapter]):
    port = EmbeddingsAdapter

    async def test_vectors_have_dims_and_keep_batch_order(self, subject: EmbeddingsAdapter) -> None:
        vectors = await subject.embed(list(TEXTS))
        assert [len(v) for v in vectors] == [subject.dims] * len(TEXTS)
        assert await subject.embed([TEXTS[2], TEXTS[0]]) == [vectors[2], vectors[0]]

    async def test_empty_batch_is_empty(self, subject: EmbeddingsAdapter) -> None:
        assert await subject.embed([]) == []

    async def test_health_is_ok(self, subject: EmbeddingsAdapter) -> None:
        assert await subject.health() == "ok"


@pytest.mark.contract
class TestVllmEmbeddingsFake(EmbeddingsContract):
    impl = "fake"
    adapter_name = "decisions.embeddings_vllm"

    @pytest.fixture
    def subject(self) -> EmbeddingsAdapter:
        from tumnis.modules.decisions.adapters.embeddings.fake import (  # noqa: PLC0415
            FakeEmbeddings,
        )

        fake = FakeEmbeddings()
        assert fake.hosted is False
        return fake


@pytest.mark.contract
class TestVllmEmbeddingsRecorded(EmbeddingsContract):
    impl = "recorded"
    adapter_name = "decisions.embeddings_vllm"

    @pytest.fixture
    def subject(self) -> EmbeddingsAdapter:
        adapter: EmbeddingsAdapter = _recorded("vllm", replay_transport(load_recordings(), []))
        assert adapter.hosted is False
        return adapter


@pytest.mark.contract
class TestHostedEmbeddingsFake(EmbeddingsContract):
    impl = "fake"
    adapter_name = "decisions.embeddings_hosted"

    @pytest.fixture
    def subject(self) -> EmbeddingsAdapter:
        from tumnis.modules.decisions.adapters.embeddings.fake import (  # noqa: PLC0415
            FakeHostedEmbeddings,
        )

        fake = FakeHostedEmbeddings()
        assert fake.hosted is True
        return fake


@pytest.mark.contract
class TestHostedEmbeddingsRecorded(EmbeddingsContract):
    impl = "recorded"
    adapter_name = "decisions.embeddings_hosted"

    @pytest.fixture
    def subject(self) -> EmbeddingsAdapter:
        adapter: EmbeddingsAdapter = _recorded(
            "hosted", replay_transport(load_recordings(), []), api_key="test-key-not-real"
        )
        assert adapter.hosted is True
        return adapter


@pytest.mark.req("FR-11.10")
@pytest.mark.wp("P3-10")
async def test_hosted_sends_its_key_as_bearer_and_local_sends_none() -> None:
    """A hosted embedder sends its key as `Authorization: Bearer`; the local one sends no
    Authorization header."""
    seen: dict[str, str | None] = {}
    inner = replay_transport(load_recordings(), [])

    async def handler(request: httpx.Request) -> httpx.Response:
        seen[str(len(seen))] = request.headers.get("authorization")
        return await inner.handle_async_request(request)

    for name, extra in (("vllm", {}), ("hosted", {"api_key": "test-key-not-real"})):
        adapter = _recorded(name, httpx.MockTransport(handler), **extra)
        await adapter.embed(list(TEXTS))
    assert seen == {"0": None, "1": "Bearer test-key-not-real"}


@pytest.mark.req("FR-11.10")
@pytest.mark.wp("P3-10")
@pytest.mark.parametrize(
    "data",
    [
        [],
        [{"index": 0, "embedding": [0.1, 0.2]}],
        [{"index": 0, "embedding": [0.1] * 1024}, {"index": 0, "embedding": [0.1] * 1024}],
        [{"index": 0}],
    ],
    ids=["missing", "wrong_dims", "repeated_index", "no_vector"],
)
async def test_answer_that_does_not_match_the_batch_is_rejected(data: list[Any]) -> None:
    """A count, an index or a dimension that does not match the batch is AdapterRejected."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"object": "list", "data": data})

    adapter = _recorded("vllm", httpx.MockTransport(handler))
    with pytest.raises(AdapterRejected):
        await adapter.embed(["one", "two"][: max(1, len(data))])
