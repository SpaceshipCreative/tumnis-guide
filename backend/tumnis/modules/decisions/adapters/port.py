"""The decisions provider port (P1-01): the protocol every decision provider implements
(Jev, the vLLM fallback of P1-02, the fake), and the typed answers it returns (defined in
the pure `rules.py`, re-exported here). Also the Generation slot's port (P1-03): one
short completion from an OpenAI-compatible endpoint.

Callers depend on these ports only. `api.py` re-exports the answer types for other
modules; `generation_api.py` re-exports `GenerationProvider`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from tumnis.core.adapters.registry import Health
from tumnis.modules.decisions.rules import ChoiceAnswer, NoulAnswer, ScoreAnswer, TypedAnswer

if TYPE_CHECKING:
    from tumnis.modules.decisions.catalog import OutboundRequest

__all__ = [
    "ChoiceAnswer",
    "DecisionsProvider",
    "NoulAnswer",
    "ProviderName",
    "ProviderResponse",
    "ScoreAnswer",
    "TypedAnswer",
]

ProviderName = Literal["jev", "vllm", "fake"]


class ProviderResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: ProviderName
    model: str  # the versioned id that answered, e.g. "jev-1.13.0"
    answers: dict[str, TypedAnswer]
    input_tokens: int
    latency_ms: int


class DecisionsProvider(Protocol):
    # The plan calls this `name`; the adapter base already uses `name` for the registry
    # name ("decisions.jev"), so the provider's short name is `provider`.
    provider: ProviderName

    async def ask(
        self, req: OutboundRequest, *, model: str, timeout_ms: int
    ) -> ProviderResponse: ...

    async def health(self) -> Health: ...


class GenerationProvider(Protocol):
    """The Generation slot (FR-11.8): a short free-text completion for the placeholder first
    action and the spoken form of a focus message, never planning or reasoning. Raises the
    adapter errors (AdapterTimeout past `timeout_ms`)."""

    async def complete(
        self, *, system: str, user: str, max_tokens: int, timeout_ms: int
    ) -> str: ...

    async def health(self) -> Health: ...
