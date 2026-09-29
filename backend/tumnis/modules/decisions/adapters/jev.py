"""`JevDecisions`: the decisions slot's primary provider, TypeSafe's Jev, over the pinned
`typesafe-sdk` behind the adapter base (P1-01, FR-11.1, FR-11.2).

The only file that imports `typesafe_sdk` (import-linter `typesafe-sdk-in-jev-only`), and
the api process never imports it (`api-never-calls-out`): the registry builds it lazily,
in the worker, when a real decision is asked.

The adapter base owns the timeout, the breaker and retries; the SDK's own retries are off.
Inside DBOS steps the workflow retries, so each `ask` sends at most once. Every request
carries the pinned model version, never an alias. The log line names the point, the
provider, both model ids, the input tokens and the latency; never the state or the
question text (and the SDK's own logger stays above DEBUG, where it would log bodies).
"""

import logging
from typing import Any, Final

import httpx2
import pydantic_core
import structlog
from pydantic import SecretStr
from typesafe_sdk import (
    AsyncTypeSafeClient,
    Choice,
    Noul,
    NoulCriteria,
    Score,
    SystemOneResponse,
    TypeSafeAPIConnectionError,
    TypeSafeAPIError,
    TypeSafeAPITimeoutError,
    TypeSafeError,
    TypeSafeRateLimitError,
)
from typesafe_sdk import ChoiceAnswer as SdkChoiceAnswer
from typesafe_sdk import RetryPolicy as SdkRetryPolicy
from typesafe_sdk import ScoreAnswer as SdkScoreAnswer

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
from tumnis.modules.decisions.adapters.port import (
    ChoiceAnswer,
    NoulAnswer,
    ProviderName,
    ProviderResponse,
    ScoreAnswer,
    TypedAnswer,
)
from tumnis.modules.decisions.catalog import ChoiceDef, NoulDef, OutboundRequest, QuestionDef
from tumnis.modules.decisions.rules import is_pinned_model

__all__ = ["JEV_POLICY", "JevDecisions", "UnpinnedModel", "wire_body"]

# The SDK logs request and response bodies at DEBUG; they must never reach a log.
logging.getLogger("typesafe_sdk").setLevel(logging.INFO)

_log = structlog.get_logger(__name__)
OP: Final = "system_one"
# One attempt per ask: callers run inside DBOS steps, which retry (P0-09). The timeout here
# is a ceiling; each ask passes its decision point's own timeout to the SDK.
JEV_POLICY: Final = CallPolicy(timeout_s=10.0, retry=RetryPolicy(max_attempts=1))
_SERVER_ERROR: Final = 500
_REQUEST_TIMEOUT: Final = 408


class UnpinnedModel(ValueError):  # noqa: N818  # the plan's name
    """A model alias (`jev-latest`) was asked for; aliases move when a release ships."""

    code = "model_not_pinned"

    def __init__(self, model: str) -> None:
        super().__init__(f"model {model!r} is not a pinned versioned id")
        self.model = model


def _to_sdk(question: QuestionDef) -> Choice | Score | Noul:
    if isinstance(question, ChoiceDef):
        return Choice(instructions=question.instructions, criteria=dict(question.criteria))
    if isinstance(question, NoulDef):
        criteria = (
            None
            if question.criteria is None
            else NoulCriteria(
                true=question.criteria.get("true"), false=question.criteria.get("false")
            )
        )
        return Noul(instructions=question.instructions, criteria=criteria)
    return Score(instructions=question.instructions, criteria=list(question.criteria))


def wire_body(req: OutboundRequest, *, model: str) -> dict[str, Any]:
    """The JSON body the SDK sends for `req`: the recordings' `request` (T-P1-01-10)."""
    body = {
        "state": req.state,
        "model": model,
        "questions": {qid: _to_sdk(q) for qid, q in req.questions.items()},
    }
    decoded: dict[str, Any] = pydantic_core.from_json(pydantic_core.to_json(body))
    return decoded


def _answer(raw: Any) -> TypedAnswer:
    if isinstance(raw, SdkChoiceAnswer):
        return ChoiceAnswer(
            choice=raw.choice, probabilities=dict(raw.probabilities), confidence=raw.confidence
        )
    if isinstance(raw, SdkScoreAnswer):
        return ScoreAnswer(
            score=raw.score,
            probabilities={str(level): p for level, p in raw.probabilities.items()},
            confidence=raw.confidence,
        )
    return NoulAnswer(noul=raw.noul)


def _from_sdk(resp: SystemOneResponse, latency_ms: int) -> ProviderResponse:
    return ProviderResponse(
        provider="jev",
        model=resp.model,
        answers={qid: _answer(answer) for qid, answer in resp.answers.items()},
        input_tokens=resp.usage.input_tokens or 0,
        latency_ms=latency_ms,
    )


def _translate(name: str, exc: TypeSafeError) -> Exception:
    """The SDK's errors as the four adapter errors (P0-09)."""
    if isinstance(exc, TypeSafeAPITimeoutError):
        return AdapterTimeout(name, OP)
    if isinstance(exc, TypeSafeAPIConnectionError):
        return AdapterUnavailable(name, OP, "connection failed")
    if isinstance(exc, TypeSafeRateLimitError):
        retry_after = None if exc.retry_after_ms is None else exc.retry_after_ms / 1000
        return AdapterUnavailable(name, OP, "rate limited (429)", retry_after_s=retry_after)
    if isinstance(exc, TypeSafeAPIError):
        if exc.status >= _SERVER_ERROR or exc.status == _REQUEST_TIMEOUT:
            return AdapterUnavailable(name, OP, f"server error ({exc.status})")
        return AdapterRejected(name, OP, f"rejected ({exc.status})")
    return AdapterRejected(name, OP, type(exc).__name__)


class JevDecisions(Adapter):
    name = "decisions.jev"
    provider: ProviderName = "jev"

    def __init__(
        self,
        api_key: SecretStr,
        pinned_model: str,
        *,
        clock: Clock,
        transport: httpx2.AsyncBaseTransport | None = None,  # MockTransport in tests
        base_url: str | None = None,
        policy: CallPolicy = JEV_POLICY,
    ) -> None:
        if not is_pinned_model(pinned_model):
            raise UnpinnedModel(pinned_model)
        super().__init__(policy=policy, clock=clock)
        self._clock = clock
        self._client = AsyncTypeSafeClient(
            api_key=api_key.get_secret_value(),
            model=pinned_model,
            retry=SdkRetryPolicy(max_retries=0),  # the adapter base owns retries and the breaker
            transport=transport,
            base_url=base_url,
        )

    async def ask(self, req: OutboundRequest, *, model: str, timeout_ms: int) -> ProviderResponse:
        if not is_pinned_model(model):
            raise UnpinnedModel(model)
        questions = {qid: _to_sdk(q) for qid, q in req.questions.items()}

        async def send() -> SystemOneResponse:
            try:
                return await self._client.system_one(
                    req.state, questions, model=model, timeout=timeout_ms / 1000
                )
            except TypeSafeError as exc:
                raise _translate(self.name, exc) from None

        started = self._clock.now()
        resp = await self.call(OP, send, idempotent=True)
        latency_ms = int((self._clock.now() - started).total_seconds() * 1000)
        out = _from_sdk(resp, latency_ms)
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
