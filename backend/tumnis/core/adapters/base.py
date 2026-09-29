"""`Adapter`: every outside call goes through `Adapter.call` (P0-09)."""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import ClassVar, Literal, TypeVar

from tumnis.core.adapters.breaker import BreakerConfig
from tumnis.core.adapters.errors import (
    AdapterError,
    AdapterRejected,
    AdapterTimeout,
    AdapterUnavailable,
    CircuitOpen,
)
from tumnis.core.adapters.retry import RetryPolicy
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
    """Base for every real adapter. Fakes do not inherit it; they implement the port directly."""

    name: ClassVar[str]

    def __init__(
        self,
        *,
        policy: CallPolicy,
        clock: Clock,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        raise NotImplementedError

    async def call(self, op: str, fn: Callable[[], Awaitable[T]], *, idempotent: bool) -> T:
        raise NotImplementedError

    def health_state(self) -> Health:
        raise NotImplementedError
