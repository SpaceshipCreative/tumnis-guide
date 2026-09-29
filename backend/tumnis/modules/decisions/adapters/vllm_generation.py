"""`VllmGeneration`: the Generation slot over an OpenAI-compatible chat completion endpoint
(local vLLM by default), behind the adapter base (P1-03, FR-11.8)."""

from typing import Final

import httpx

from tumnis.core.adapters.base import Adapter, CallPolicy, Health
from tumnis.core.adapters.retry import RetryPolicy
from tumnis.core.clock import Clock
from tumnis.core.net import NetPolicy, Resolver, system_resolver

__all__ = ["GENERATION_POLICY", "VllmGeneration"]

GENERATION_POLICY: Final = CallPolicy(timeout_s=10.0, retry=RetryPolicy(max_attempts=1))


class VllmGeneration(Adapter):
    name = "decisions.vllm_generation"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        clock: Clock,
        net_policy: NetPolicy,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,  # MockTransport in tests
        policy: CallPolicy = GENERATION_POLICY,
    ) -> None:
        super().__init__(policy=policy, clock=clock)
        self._base_url = base_url
        self._model = model
        self._net_policy = net_policy
        self._resolver = resolver
        self._transport = transport

    async def complete(self, *, system: str, user: str, max_tokens: int, timeout_ms: int) -> str:
        raise NotImplementedError

    async def health(self) -> Health:
        raise NotImplementedError
