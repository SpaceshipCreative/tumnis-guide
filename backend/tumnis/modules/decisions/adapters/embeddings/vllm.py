"""`VllmEmbeddings`: the Embeddings slot over an OpenAI-compatible `/v1/embeddings` endpoint
(the local vLLM by default), behind the adapter base (P3-10, FR-11.10).

Request `{"model", "input": [...], "encoding_format": "float"}`; the answer's `data` items
carry `index` and `embedding` (vLLM's OpenAI-compatible server, as OpenAI's API), and are
put back in the order sent whatever order the server lists them in. A count or dimension
that does not match is refused (AdapterRejected). An empty batch is answered with [] and
nothing is sent. Calls go through the SSRF-guarded client (`openai_compat.ChatEndpoint`
with the embeddings path).

The api process never imports this file (import-linter `api-never-calls-out`): the
registry builds it lazily. The log line names the model, the batch size and the latency;
never the text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import structlog

from tumnis.core.adapters.base import Adapter, CallPolicy, Health
from tumnis.core.adapters.errors import AdapterRejected
from tumnis.core.adapters.retry import RetryPolicy
from tumnis.core.net import NetPolicy, Resolver, system_resolver
from tumnis.modules.decisions.adapters.openai_compat import ChatEndpoint

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx

    from tumnis.core.clock import Clock

__all__ = ["EMBEDDINGS_PATH", "EMBEDDINGS_POLICY", "VllmEmbeddings", "embeddings_body"]

_log = structlog.get_logger(__name__)
OP: Final = "embeddings"
EMBEDDINGS_PATH: Final = "/v1/embeddings"
# One attempt per call: index-time and re-embed callers run inside DBOS steps (which retry)
# and a search falls back to full text. The timeout is a ceiling for a batch of 64 chunks.
EMBEDDINGS_POLICY: Final = CallPolicy(timeout_s=30.0, retry=RetryPolicy(max_attempts=1))


def embeddings_body(model: str, texts: Sequence[str]) -> dict[str, Any]:
    """The request body: the recordings' `request`."""
    return {"model": model, "input": list(texts), "encoding_format": "float"}


def _vectors(payload: dict[str, Any], count: int, dims: int, *, adapter: str) -> list[list[float]]:
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != count:
        raise AdapterRejected(adapter, OP, f"expected {count} embeddings")
    out: list[list[float] | None] = [None] * count
    for item in data:
        try:
            index, vector = item["index"], item["embedding"]
        except (KeyError, TypeError):
            raise AdapterRejected(adapter, OP, "an embedding has no index or vector") from None
        if not isinstance(index, int) or not 0 <= index < count or out[index] is not None:
            raise AdapterRejected(adapter, OP, "embedding indexes do not match the batch")
        if not isinstance(vector, list) or len(vector) != dims:
            raise AdapterRejected(adapter, OP, f"an embedding is not {dims} numbers")
        out[index] = [float(x) for x in vector]
    return [vector for vector in out if vector is not None]


class VllmEmbeddings(Adapter):
    name = "decisions.embeddings_vllm"
    hosted: bool = False

    def __init__(  # the endpoint, the model, and the injected services
        self,
        base_url: str,
        model: str,
        dims: int,
        *,
        clock: Clock,
        net_policy: NetPolicy,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,  # MockTransport in tests
        policy: CallPolicy = EMBEDDINGS_POLICY,
        api_key: str | None = None,
    ) -> None:
        super().__init__(policy=policy, clock=clock)
        self._clock = clock
        self.model = model
        self.dims = dims
        self._endpoint = ChatEndpoint(
            base_url,
            adapter=self.name,
            op=OP,
            net_policy=net_policy,
            timeout_s=policy.timeout_s,
            resolver=resolver,
            transport=transport,
            path=EMBEDDINGS_PATH,
            bearer=api_key,
        )
        self._timeout_s = policy.timeout_s

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        body = embeddings_body(self.model, texts)

        async def send() -> dict[str, Any]:
            return await self._endpoint.post(body, timeout_s=self._timeout_s)

        started = self._clock.now()
        payload = await self.call(OP, send, idempotent=True)
        vectors = _vectors(payload, len(texts), self.dims, adapter=self.name)
        _log.info(
            "decisions.embeddings_call",
            provider="hosted" if self.hosted else "vllm",
            model=self.model,
            batch=len(texts),
            latency_ms=int((self._clock.now() - started).total_seconds() * 1000),
        )
        return vectors

    async def health(self) -> Health:
        return self.health_state()

    async def aclose(self) -> None:
        await self._endpoint.aclose()
