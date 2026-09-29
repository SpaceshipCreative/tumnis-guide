"""`VllmGeneration`: the Generation slot over an OpenAI-compatible chat completion endpoint
(local vLLM by default), behind the adapter base (P1-03, FR-11.8).

One short, low-temperature completion per call, sent through the SSRF-guarded client
(`openai_compat.ChatEndpoint`). One attempt per call: the slot's callers wait at most a
couple of seconds and show a pending state instead of retrying. Only `generation_api`
asks it (import-linter `generation-callers`), and the registry builds it lazily in the
worker. The log line names the model, token counts and latency; never the prompt or the
answer.
"""

from typing import Any, Final

import httpx
import structlog

from tumnis.core.adapters.base import Adapter, CallPolicy, Health
from tumnis.core.adapters.retry import RetryPolicy
from tumnis.core.clock import Clock
from tumnis.core.net import NetPolicy, Resolver, system_resolver
from tumnis.modules.decisions.adapters.openai_compat import ChatEndpoint, message_text

__all__ = ["GENERATION_POLICY", "TEMPERATURE", "VllmGeneration", "chat_body"]

_log = structlog.get_logger(__name__)
OP: Final = "chat_completion"
# A ceiling only: each call passes its caller's own timeout (2 s for placeholders).
GENERATION_POLICY: Final = CallPolicy(timeout_s=10.0, retry=RetryPolicy(max_attempts=1))
TEMPERATURE: Final = 0.2  # plan default: short, plain, repeatable


def chat_body(model: str, *, system: str, user: str, max_tokens: int) -> dict[str, Any]:
    """The request body: the recordings' `request.body`."""
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": TEMPERATURE,
        "n": 1,
        "stream": False,
    }


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
        self._clock = clock
        self._model = model
        self._endpoint = ChatEndpoint(
            base_url,
            adapter=self.name,
            op=OP,
            net_policy=net_policy,
            timeout_s=policy.timeout_s,
            resolver=resolver,
            transport=transport,
        )

    async def complete(self, *, system: str, user: str, max_tokens: int, timeout_ms: int) -> str:
        body = chat_body(self._model, system=system, user=user, max_tokens=max_tokens)

        async def send() -> dict[str, Any]:
            return await self._endpoint.post(body, timeout_s=timeout_ms / 1000)

        started = self._clock.now()
        payload = await self.call(OP, send, idempotent=True)
        text = message_text(payload, adapter=self.name, op=OP)
        usage = payload.get("usage")
        if not isinstance(usage, dict):  # metadata only: never worth losing the answer
            usage = {}
        _log.info(
            "decisions.generation_call",
            provider="vllm",
            model=self._model,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            latency_ms=int((self._clock.now() - started).total_seconds() * 1000),
        )
        return text

    async def health(self) -> Health:
        return self.health_state()

    async def aclose(self) -> None:
        await self._endpoint.aclose()
