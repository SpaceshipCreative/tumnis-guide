"""`FakeDecisions`: the decisions provider fake (P1-01), scripted per decision point.

Unscripted questions get deterministic answers of the right primitive (a Choice picks its
first option at 0.7, a Score its middle level at 0.7, a Noul says 0.5). `script(point,
answers, latency_ms=..., fail=...)` sets what a point answers, how long it takes (a latency
over the call's timeout raises AdapterTimeout, like the real one) and whether it fails;
`calls` records every ask.

Across processes (R-37): `POST /v1/test/fakes/{hook}/script` stores a script that
`FakeDecisions` (hooks `decisions.jev` and `decisions.vllm`) and `FakeGeneration` (hook
`generation`) read through tumnis.core.fake_scripts on every call while the store is
enabled; a stored script replaces the in-memory one. `parse_decisions_script` and
`parse_generation_script` say what the route accepts.
"""

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from tumnis.core import fake_scripts
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
    CATALOGUE,
    ChoiceDef,
    DecisionPoint,
    NoulDef,
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


def _spread(keys: list[str], winner: str, lead: float = LEAD) -> dict[str, float]:
    rest = (1.0 - lead) / (len(keys) - 1)
    return {key: lead if key == winner else rest for key in keys}


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


# --- Stored scripts (R-37) ----------------------------------------------------------------


class _Stored(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StoredDecisionsScript(_Stored):
    """What `POST /v1/test/fakes/decisions.jev/script` (or `decisions.vllm`) takes.

    `question` names the decision point (the journeys' word for it). `answer` scripts the
    point's main question: a Choice's option key, a Score's level index ("0", "1", ...),
    a Noul's probability of yes; a Choice or Score answer wins at `confidence` (default
    0.7) and the other options share the rest. `answers` gives full typed answers by
    question id instead. Unscripted questions get the default answer.
    """

    question: DecisionPoint
    answer: str | float | None = None
    confidence: float | None = Field(default=None, gt=0, le=1)
    answers: dict[str, TypedAnswer] = Field(default_factory=dict)
    latency_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _noul_answer_is_a_probability(self) -> Self:
        """A Noul point's primitive is fixed in the catalogue, so its shorthand is checked
        when posted: a number from 0 to 1."""
        if self.answer is None or CATALOGUE[self.question].primitive != "noul":
            return self
        try:
            value = float(self.answer)
        except ValueError:
            value = math.nan
        if not 0 <= value <= 1:
            raise ValueError(
                f"{self.question.value} answers a probability from 0 to 1, not {self.answer!r}"
            )
        return self


def parse_decisions_script(body: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """The route's parser: the match key is the decision point."""
    parsed = StoredDecisionsScript.model_validate(body)
    return parsed.question.value, parsed.model_dump(mode="json")


def _shorthand(qid: str, question: QuestionDef, answer: str | float, lead: float) -> TypedAnswer:
    if isinstance(question, NoulDef):
        return NoulAnswer(noul=float(answer))
    keys = (
        list(question.criteria)
        if isinstance(question, ChoiceDef)
        else [str(n) for n in range(len(question.criteria))]
    )
    winner = str(answer)
    if winner not in keys:
        raise ValueError(f"scripted answer {winner!r} is not an option of {qid!r} ({keys})")
    probabilities = _spread(keys, winner, lead)
    if isinstance(question, ChoiceDef):
        return ChoiceAnswer(choice=winner, probabilities=probabilities, confidence=lead)
    score = sum(int(level) * p for level, p in probabilities.items())
    return ScoreAnswer(score=score, probabilities=probabilities, confidence=lead)


def scripted(stored: Mapping[str, Any], questions: Mapping[str, QuestionDef]) -> Script:
    """The fake's script for one ask from a stored script and the asked questions. A
    shorthand answer the main question does not offer raises ValueError: a mis-scripted
    test fails loudly instead of passing on a default answer."""
    parsed = StoredDecisionsScript.model_validate(stored)
    answers = dict(parsed.answers)
    if parsed.answer is not None:
        main = CATALOGUE[parsed.question].main_question
        if main not in questions:
            raise ValueError(f"{parsed.question.value} asks no {main!r}; script `answers`")
        lead = parsed.confidence or LEAD
        answers[main] = _shorthand(main, questions[main], parsed.answer, lead)
    return Script(answers, parsed.latency_ms)


class FakeDecisions:
    provider: ProviderName = "fake"

    def __init__(
        self,
        *,
        hook: str = "decisions.jev",
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """`hook`: the name its stored scripts are posted under (R-37)."""
        self.hook = hook
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
        stored = await fake_scripts.lookup(self.hook, req.point.value)
        if stored is not None:
            script = scripted(stored, req.questions)
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


GENERATION_HOOK = "generation"


class StoredGenerationScript(_Stored):
    """What `POST /v1/test/fakes/generation/script` takes: the text the Generation slot
    answers (the task's first action) after `delay_ms`."""

    first_action: str = Field(min_length=1)
    delay_ms: int = Field(default=0, ge=0)


def parse_generation_script(body: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """The route's parser: one script for the slot."""
    return "", StoredGenerationScript.model_validate(body).model_dump(mode="json")


def fake_vllm(**deps: Any) -> FakeDecisions:
    """The vLLM fallback's fake: scripted apart from Jev's, under `decisions.vllm`."""
    return FakeDecisions(hook="decisions.vllm", **deps)


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
        text, delay_ms, fail = self.text, self.delay_ms, self.fail
        stored = await fake_scripts.lookup(GENERATION_HOOK)
        if stored is not None:
            # A stored script replaces the whole in-memory one, failure included.
            parsed = StoredGenerationScript.model_validate(stored)
            text, delay_ms, fail = parsed.first_action, parsed.delay_ms, None
        if delay_ms > timeout_ms:
            await self._sleep(timeout_ms / 1000)
            raise AdapterTimeout("decisions.vllm_generation", "chat_completion")
        if delay_ms:
            await self._sleep(delay_ms / 1000)
        if fail is not None:
            raise fail
        return text

    def health_state(self) -> Health:
        return self._health

    async def health(self) -> Health:
        return self._health
