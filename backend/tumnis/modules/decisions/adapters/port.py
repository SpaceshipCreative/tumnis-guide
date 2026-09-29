"""The decisions provider port (P1-01): the protocol every decision provider implements
(Jev now; the vLLM fallback in P1-02; the fake), and the typed answers it returns. Also the
Generation slot's port (P1-03): one short completion from an OpenAI-compatible endpoint.

Callers depend on these ports only. `api.py` re-exports the answer types for other
modules; `generation_api.py` re-exports `GenerationProvider`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from tumnis.core.adapters.registry import Health

if TYPE_CHECKING:
    from tumnis.modules.decisions.catalog import OutboundRequest

ProviderName = Literal["jev", "vllm", "fake"]


class _Answer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ChoiceAnswer(_Answer):
    type: Literal["choice"] = "choice"
    choice: str
    probabilities: dict[str, float]  # option key -> probability; sums to 1
    confidence: float


class ScoreAnswer(_Answer):
    type: Literal["score"] = "score"
    score: float  # expected level: the probability-weighted mean of the level indices
    probabilities: dict[str, float]  # level index ("0", "1", ...) -> probability; sums to 1
    confidence: float


class NoulAnswer(_Answer):
    type: Literal["noul"] = "noul"
    noul: float  # probability of yes; Nouls carry no confidence (routed on bands, P1-02)


TypedAnswer = Annotated[ChoiceAnswer | ScoreAnswer | NoulAnswer, Field(discriminator="type")]


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
