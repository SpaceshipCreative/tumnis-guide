"""The Embeddings slot's port (P3-10, FR-11.10): vectors for chunk and query text from an
OpenAI-compatible `/v1/embeddings` endpoint (a local vLLM by default; a hosted provider
optionally). Callers depend on this protocol only; `decisions.api.embed` routes between
the configured adapters (a local-only project never reaches a hosted one)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from tumnis.core.adapters.registry import Health

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["EmbeddingsAdapter"]


class EmbeddingsAdapter(Protocol):
    model: str  # the served model name, e.g. "BAAI/bge-m3"
    dims: int  # every vector's length
    hosted: bool  # True: the text leaves the deployment (Data flow rule 6)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in the order sent; [] for no texts, with no call."""
        ...

    async def health(self) -> Health: ...
