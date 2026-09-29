"""Circuit breaker: a pure transition function and a stateful wrapper on a `Clock` (P0-09)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from tumnis.core.clock import Clock

State = Literal["closed", "open", "half_open"]
Event = Literal["request", "success", "failure"]


@dataclass(frozen=True)
class BreakerConfig:
    failure_threshold: int = 5  # plan default
    cooldown_s: float = 30.0  # plan default
    half_open_max_calls: int = 1


@dataclass(frozen=True)
class BreakerState:
    state: State = "closed"
    consecutive_failures: int = 0
    opened_at: datetime | None = None
    half_open_in_flight: int = 0


def next_state(
    s: BreakerState, event: Event, now: datetime, cfg: BreakerConfig
) -> tuple[BreakerState, bool]:
    raise NotImplementedError


class CircuitBreaker:
    def __init__(self, cfg: BreakerConfig, clock: Clock, *, name: str = "") -> None:
        raise NotImplementedError

    def before_call(self, op: str = "") -> None:
        raise NotImplementedError

    def on_success(self) -> None:
        raise NotImplementedError

    def on_failure(self) -> None:
        raise NotImplementedError

    @property
    def state(self) -> State:
        raise NotImplementedError
