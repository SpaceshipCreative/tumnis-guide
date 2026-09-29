"""Circuit breaker: a pure transition function and a stateful wrapper on a `Clock` (P0-09).

One breaker per adapter instance, and one instance per (adapter, workspace connection)
where credentials differ, so one workspace's broken token does not open the circuit for
another workspace.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal

from tumnis.core.adapters.errors import CircuitOpen
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


CLOSED = BreakerState()


def next_state(
    s: BreakerState, event: Event, now: datetime, cfg: BreakerConfig
) -> tuple[BreakerState, bool]:
    """Pure. Returns (new state, allowed); `allowed` matters only for "request".

    request: closed allows; open allows once the cooldown has elapsed (moving to half_open
    and counting the trial in flight); half_open allows while fewer than
    `half_open_max_calls` trials are in flight.
    success: closes and resets from any state but open (a late answer to a call made before
    the circuit opened does not close it).
    failure: closed counts it and opens at the threshold; half_open reopens and restarts
    the cooldown; open stays open.
    """
    if event == "request":
        return _request(s, now, cfg)
    if event == "success":
        return (s if s.state == "open" else CLOSED), True
    return _failure(s, now, cfg), True


def _request(s: BreakerState, now: datetime, cfg: BreakerConfig) -> tuple[BreakerState, bool]:
    if s.state == "closed":
        return s, True
    if s.state == "open":
        cooling = s.opened_at is not None and now - s.opened_at < timedelta(seconds=cfg.cooldown_s)
        if cooling:
            return s, False
        return replace(s, state="half_open", half_open_in_flight=1), True
    if s.half_open_in_flight < cfg.half_open_max_calls:
        return replace(s, half_open_in_flight=s.half_open_in_flight + 1), True
    return s, False


def _failure(s: BreakerState, now: datetime, cfg: BreakerConfig) -> BreakerState:
    if s.state == "open":
        return s
    failures = s.consecutive_failures + 1
    if s.state == "half_open" or failures >= cfg.failure_threshold:
        return BreakerState(state="open", consecutive_failures=failures, opened_at=now)
    return replace(s, consecutive_failures=failures)


class CircuitBreaker:
    """`next_state` on a clock. `before_call` raises `CircuitOpen` when a call is refused."""

    def __init__(self, cfg: BreakerConfig, clock: Clock, *, name: str = "") -> None:
        self._cfg = cfg
        self._clock = clock
        self._name = name
        self._s = CLOSED

    def before_call(self, op: str = "") -> None:
        self._s, allowed = next_state(self._s, "request", self._clock.now(), self._cfg)
        if not allowed:
            raise CircuitOpen(self._name, op)

    def on_success(self) -> None:
        self._s, _ = next_state(self._s, "success", self._clock.now(), self._cfg)

    def on_failure(self) -> None:
        self._s, _ = next_state(self._s, "failure", self._clock.now(), self._cfg)

    @property
    def state(self) -> State:
        return self._s.state

    @property
    def snapshot(self) -> BreakerState:
        return self._s
