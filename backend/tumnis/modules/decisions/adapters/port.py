"""The decisions provider port (P1-01): the protocol every decision provider implements
(Jev, the vLLM fallback of P1-02, the fake), and the typed answers it returns (defined in
the pure `rules.py`, re-exported here).

Callers depend on this port only. `api.py` re-exports the answer types for other modules.
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
