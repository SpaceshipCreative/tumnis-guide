"""`FakeDecisions`: the decisions provider fake (P1-01), scripted per decision point.

Unscripted questions get deterministic answers of the right primitive (a Choice picks its
first option at 0.7, a Score its middle level at 0.7, a Noul says 0.5). `script(point,
answers, latency_ms=..., fail=...)` sets what a point answers, how long it takes (a latency
over the call's timeout raises AdapterTimeout, like the real one) and whether it fails;
`calls` records every ask.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import TypeAdapter

from tumnis.core.adapters.errors import AdapterError, AdapterTimeout
from tumnis.core.adapters.registry import Health
from tumnis.modules.decisions.adapters.port import (
    ChoiceAnswer,
    NoulAnswer,
    ProviderName,
    ProviderResponse,
    ScoreAnswer,
    TypedAnswer,
)
from tumnis.modules.decisions.catalog import (
    DecisionPoint,
    OutboundRequest,
    QuestionDef,
    estimate_tokens,
)

_ANSWER: TypeAdapter[TypedAnswer] = TypeAdapter(TypedAnswer)
LEAD: float = 0.7  # the default answer's winning probability


@dataclass(frozen=True)
class FakeCall:
    point: DecisionPoint
    request: OutboundRequest
    model: str
    timeout_ms: int


@dataclass
class Script:
    answers: dict[str, TypedAnswer] = field(default_factory=dict)
    latency_ms: int = 0
    fail: AdapterError | None = None


def _spread(keys: list[str], winner: str) -> dict[str, float]:
    rest = (1.0 - LEAD) / (len(keys) - 1)
    return {key: LEAD if key == winner else rest for key in keys}


def default_answer(question: QuestionDef) -> TypedAnswer:
    """A deterministic, well-formed answer for any question."""
    if question.type == "choice":
        options = list(question.criteria)
        return ChoiceAnswer(
            choice=options[0], probabilities=_spread(options, options[0]), confidence=LEAD
        )
    if question.type == "score":
        levels = [str(n) for n in range(len(question.criteria))]
        middle = levels[(len(levels) - 1) // 2]
        probabilities = _spread(levels, middle)
        score = sum(int(level) * p for level, p in probabilities.items())
        return ScoreAnswer(score=score, probabilities=probabilities, confidence=LEAD)
    return NoulAnswer(noul=0.5)


class FakeDecisions:
    provider: ProviderName = "fake"

    def __init__(self, *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self._sleep = sleep
        self._scripts: dict[DecisionPoint, Script] = {}
        self._health: Health = "ok"
        self.calls: list[FakeCall] = []

    def script(
        self,
        point: DecisionPoint | str,
        answers: Mapping[str, TypedAnswer | Mapping[str, Any]] | None = None,
        *,
        latency_ms: int = 0,
        fail: AdapterError | None = None,
    ) -> None:
        """What `point` answers from now on: the given answers by question id (others get
        the default), after `latency_ms`, or `fail` raised instead."""
        parsed = {
            qid: answer if not isinstance(answer, Mapping) else _ANSWER.validate_python(answer)
            for qid, answer in (answers or {}).items()
        }
        self._scripts[DecisionPoint(point)] = Script(parsed, latency_ms, fail)

    def set_health(self, state: Health) -> None:
        self._health = state

    def reset(self) -> None:
        self._scripts.clear()
        self.calls.clear()
        self._health = "ok"

    async def ask(self, req: OutboundRequest, *, model: str, timeout_ms: int) -> ProviderResponse:
        self.calls.append(FakeCall(req.point, req, model, timeout_ms))
        script = self._scripts.get(req.point, Script())
        if script.latency_ms > timeout_ms:
            await self._sleep(timeout_ms / 1000)
            raise AdapterTimeout("decisions.fake", "system_one")
        if script.latency_ms:
            await self._sleep(script.latency_ms / 1000)
        if script.fail is not None:
            raise script.fail
        answers = {
            qid: script.answers.get(qid) or default_answer(question)
            for qid, question in req.questions.items()
        }
        return ProviderResponse(
            provider=self.provider,
            model=model,
            answers=answers,
            input_tokens=estimate_tokens(req),
            latency_ms=script.latency_ms,
        )

    def health_state(self) -> Health:
        return self._health

    async def health(self) -> Health:
        return self._health


# --- Generation slot (P1-03) ------------------------------------------------------------


@dataclass(frozen=True)
class GenerationCall:
    system: str
    user: str
    max_tokens: int
    timeout_ms: int


class FakeGeneration:
    """The Generation slot's fake (P1-03): answers `text` after `delay_ms`, or raises `fail`.

    A delay over the call's `timeout_ms` sleeps the timeout and raises AdapterTimeout, like
    the real adapter. `script(...)` changes the answer; `calls` records every prompt.
    """

    DEFAULT_TEXT = "Open the task and write down the first concrete step."

    def __init__(
        self,
        *,
        text: str = DEFAULT_TEXT,
        delay_ms: int = 0,
        fail: AdapterError | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.text = text
        self.delay_ms = delay_ms
        self.fail = fail
        self._sleep = sleep
        self._health: Health = "ok"
        self.calls: list[GenerationCall] = []

    def script(
        self, text: str = DEFAULT_TEXT, *, delay_ms: int = 0, fail: AdapterError | None = None
    ) -> None:
        self.text, self.delay_ms, self.fail = text, delay_ms, fail

    def set_health(self, state: Health) -> None:
        self._health = state

    def reset(self) -> None:
        self.script()
        self.calls.clear()
        self._health = "ok"

    async def complete(self, *, system: str, user: str, max_tokens: int, timeout_ms: int) -> str:
        self.calls.append(GenerationCall(system, user, max_tokens, timeout_ms))
        if self.delay_ms > timeout_ms:
            await self._sleep(timeout_ms / 1000)
            raise AdapterTimeout("decisions.vllm_generation", "chat_completion")
        if self.delay_ms:
            await self._sleep(self.delay_ms / 1000)
        if self.fail is not None:
            raise self.fail
        return self.text

    def health_state(self) -> Health:
        return self._health

    async def health(self) -> Health:
        return self._health
