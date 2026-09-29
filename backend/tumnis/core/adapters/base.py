"""`Adapter`: every outside call goes through `Adapter.call` (P0-09)."""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import ClassVar, Literal, TypeVar

from tumnis.core.adapters.breaker import BreakerConfig, CircuitBreaker
from tumnis.core.adapters.errors import (
    AdapterError,
    AdapterRejected,
    AdapterTimeout,
    AdapterUnavailable,
    CircuitOpen,
)
from tumnis.core.adapters.retry import RetryPolicy, delay_for
from tumnis.core.clock import Clock

__all__ = [
    "Adapter",
    "AdapterError",
    "AdapterRejected",
    "AdapterTimeout",
    "AdapterUnavailable",
    "CallPolicy",
    "CircuitOpen",
    "Health",
]

T = TypeVar("T")
Health = Literal["ok", "degraded"]


@dataclass(frozen=True)
class CallPolicy:
    timeout_s: float
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    breaker: BreakerConfig = field(default_factory=BreakerConfig)


class Adapter:
    """Base for every real adapter. Fakes do not inherit it; they implement the port directly.

    `name` is the registry name ("decisions.jev"). One breaker per instance: build one
    instance per (adapter, workspace connection) where credentials differ.
    """

    name: ClassVar[str]

    def __init__(
        self,
        *,
        policy: CallPolicy,
        clock: Clock,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self._policy = policy
        self._sleep = sleep
        self._rand = rand
        self._breaker = CircuitBreaker(policy.breaker, clock, name=self.name)

    async def call(self, op: str, fn: Callable[[], Awaitable[T]], *, idempotent: bool) -> T:
        """Run `fn` under the timeout and the breaker, retrying retryable errors.

        `fn` translates its library's exceptions into the four adapter errors. Only
        idempotent calls are retried, at most `retry.max_attempts` attempts in all. Every
        failed attempt counts against the breaker, so a flapping provider opens it quickly.
        """
        attempt = 1
        while True:
            try:
                return await self._attempt(op, fn)
            except AdapterError as err:
                retry = self._policy.retry
                if not (err.retryable and idempotent and attempt < retry.max_attempts):
                    raise
                await self._sleep(delay_for(retry, attempt, self._rand, err.retry_after_s))
            attempt += 1

    async def _attempt(self, op: str, fn: Callable[[], Awaitable[T]]) -> T:
        self._breaker.before_call(op)  # CircuitOpen: nothing is sent, not retryable
        try:
            async with asyncio.timeout(self._policy.timeout_s):
                result = await fn()
        except TimeoutError:
            self._breaker.on_failure()
            raise AdapterTimeout(self.name, op) from None
        except AdapterError as exc:
            if exc.retryable:
                self._breaker.on_failure()
            else:
                self._breaker.on_success()  # the provider answered: not an outage
            raise
        except BaseException:
            # An untranslated error or a cancellation still ends the call, so a half-open
            # trial slot is never left taken.
            self._breaker.on_failure()
            raise
        self._breaker.on_success()
        return result

    def health_state(self) -> Health:
        """Degraded while the breaker is not closed."""
        return "ok" if self._breaker.state == "closed" else "degraded"
