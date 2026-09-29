"""`VllmDecisions`: the decisions slot's fallback, a local vLLM behind its OpenAI-compatible
API (P1-02, FR-11.3), used when Jev is down and for projects set to local decisions only.

vLLM returns no calibrated probabilities, so each question is one chat completion sampled
`SAMPLES` times at `TEMPERATURE`, constrained to the allowed answers with vLLM's
`structured_outputs` field (`guided_*` was removed in v0.12.0), and the votes become the
typed answer through the pure `vote_answer`. The adapter base owns the timeout, the
breaker and retries; the calls go through the SSRF-guarded client (a self-hosted vLLM is
private, so the deployment mode decides whether that address may be reached).

The api process never imports this file (import-linter `api-never-calls-out`): the
registry builds it lazily, in the worker. The log line names the point, the provider, the
model and the latency; never the state or the question text.
"""

import asyncio
import json
from dataclasses import replace
from typing import Any, Final

import httpx
import structlog

from tumnis.core.adapters.base import (
    Adapter,
    AdapterRejected,
    AdapterTimeout,
    AdapterUnavailable,
    CallPolicy,
    Health,
)
from tumnis.core.adapters.retry import RetryPolicy
from tumnis.core.clock import Clock
from tumnis.core.net import NetPolicy, Resolver, guarded_client, system_resolver
from tumnis.modules.decisions.adapters.port import ProviderName, ProviderResponse, TypedAnswer
from tumnis.modules.decisions.catalog import ChoiceDef, NoulDef, OutboundRequest, QuestionDef
from tumnis.modules.decisions.rules import vote_answer

__all__ = ["VLLM_POLICY", "VllmDecisions", "chat_bodies"]

_log = structlog.get_logger(__name__)
OP: Final = "chat_completions"
PATH: Final = "/v1/chat/completions"
SAMPLES: Final = 5  # plan default
TEMPERATURE: Final = 0.7  # plan default
MAX_TOKENS: Final = 8  # the longest allowed answer is an option key such as `p254`
SYSTEM_PROMPT: Final = (
    "You answer one classification question about the state you are given. "
    "Reply with exactly one of the allowed answers and nothing else."
)
# One attempt per ask: callers run inside DBOS steps, which retry (P0-09). The timeout here
# is a ceiling; each ask passes its decision point's own timeout to the request.
VLLM_POLICY: Final = CallPolicy(timeout_s=10.0, retry=RetryPolicy(max_attempts=1))
_SERVER_ERROR: Final = 500
_REQUEST_TIMEOUT: Final = 408
_TOO_MANY: Final = 429


def _allowed(question: QuestionDef) -> list[str]:
    """The answers vLLM may return, in the order the question lists them."""
    if question.type == "choice":
        return list(question.criteria)
    if question.type == "score":
        return [str(level) for level in range(len(question.criteria))]
    return ["yes", "no"]


def _text(value: str | dict[str, Any]) -> str:
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def _describe(question: QuestionDef) -> list[str]:
    """One `answer: meaning` line per allowed answer."""
    if isinstance(question, ChoiceDef):
        return [f"- {key}: {_text(meaning)}" for key, meaning in question.criteria.items()]
    if isinstance(question, NoulDef):
        meaning = question.criteria or {}
        return [
            f"- yes: {meaning.get('true', 'the statement is true')}",
            f"- no: {meaning.get('false', 'the statement is false')}",
        ]
    return [f"- {level}: {meaning}" for level, meaning in enumerate(question.criteria)]


def _prompt(req: OutboundRequest, question: QuestionDef) -> str:
    state = json.dumps(req.state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    lines = [
        f"Question: {_text(question.instructions)}",
        "Allowed answers:",
        *_describe(question),
        f"State (JSON): {state}",
        "Allowed answers, exactly one of: " + ", ".join(_allowed(question)),
    ]
    return "\n".join(lines)


def chat_bodies(req: OutboundRequest, *, model: str) -> dict[str, dict[str, Any]]:
    """The chat completion body sent for each question id of `req`: the recordings'
    `request` (T-P1-02-15). `structured_outputs` is a top-level field, as vLLM reads it."""
    return {
        qid: {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _prompt(req, question)},
            ],
            "n": SAMPLES,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
            "structured_outputs": {"choice": _allowed(question)},
        }
        for qid, question in req.questions.items()
    }


def _votes(name: str, body: Any) -> tuple[list[str], str, int]:
    """(the sampled answers, the model that answered, the prompt tokens) of one reply."""
    try:
        choices = body["choices"]
        samples = [str(choice["message"]["content"]).strip() for choice in choices]
        model = str(body["model"])
        usage = body.get("usage") or {}
        tokens = int(usage.get("prompt_tokens") or 0)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise AdapterRejected(name, OP, "malformed chat completion") from None
    return samples, model, tokens


def _translate(name: str, response: httpx.Response) -> None:
    """Raise the adapter error for a non-200 answer."""
    status = response.status_code
    if status == _TOO_MANY:
        try:
            retry_after = float(response.headers.get("retry-after", ""))
        except ValueError:
            retry_after = None
        raise AdapterUnavailable(name, OP, "rate limited (429)", retry_after_s=retry_after)
    if status >= _SERVER_ERROR or status == _REQUEST_TIMEOUT:
        raise AdapterUnavailable(name, OP, f"server error ({status})")
    raise AdapterRejected(name, OP, f"rejected ({status})")


class VllmDecisions(Adapter):
    name = "decisions.vllm"
    provider: ProviderName = "vllm"

    def __init__(
        self,
        base_url: str,
        *,
        clock: Clock,
        net_policy: NetPolicy,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,  # MockTransport in tests
        policy: CallPolicy = VLLM_POLICY,
    ) -> None:
        super().__init__(policy=policy, clock=clock)
        self._clock = clock
        target = httpx.URL(base_url)
        ports = net_policy.ports if target.port is None else net_policy.ports | {target.port}
        self._url = str(target.copy_with(path=PATH))
        self._client = guarded_client(
            replace(net_policy, ports=ports),
            timeout=policy.timeout_s,
            resolver=resolver,
            inner=transport,
        )

    async def _ask_one(
        self, qid: str, question: QuestionDef, body: dict[str, Any], timeout_s: float
    ) -> tuple[TypedAnswer, str, int]:
        try:
            response = await self._client.post(self._url, json=body, timeout=timeout_s)
        except httpx.TimeoutException:
            raise AdapterTimeout(self.name, OP) from None
        except httpx.TransportError:
            raise AdapterUnavailable(self.name, OP, "connection failed") from None
        if response.status_code != httpx.codes.OK:
            _translate(self.name, response)
        try:
            payload = response.json()
        except ValueError:
            raise AdapterRejected(self.name, OP, "malformed chat completion") from None
        samples, model, tokens = _votes(self.name, payload)
        try:
            return vote_answer(question, samples), model, tokens
        except ValueError:
            raise AdapterRejected(self.name, OP, f"no valid answer to {qid}") from None

    async def ask(self, req: OutboundRequest, *, model: str, timeout_ms: int) -> ProviderResponse:
        bodies = chat_bodies(req, model=model)

        async def send() -> list[tuple[TypedAnswer, str, int]]:
            tasks = [
                asyncio.ensure_future(
                    self._ask_one(qid, question, bodies[qid], timeout_ms / 1000)
                )
                for qid, question in req.questions.items()
            ]
            try:
                return list(await asyncio.gather(*tasks))
            except BaseException:
                for task in tasks:
                    task.cancel()
                raise

        started = self._clock.now()
        results = await self.call(OP, send, idempotent=True)
        latency_ms = int((self._clock.now() - started).total_seconds() * 1000)
        out = ProviderResponse(
            provider=self.provider,
            model=results[0][1],
            answers=dict(zip(req.questions, (answer for answer, _, _ in results), strict=True)),
            input_tokens=sum(tokens for _, _, tokens in results),
            latency_ms=latency_ms,
        )
        _log.info(
            "decisions.provider_call",
            point=req.point.value,
            provider=self.provider,
            model_requested=model,
            model_answered=out.model,
            input_tokens=out.input_tokens,
            latency_ms=latency_ms,
        )
        return out

    async def health(self) -> Health:
        return self.health_state()

    async def aclose(self) -> None:
        await self._client.aclose()

