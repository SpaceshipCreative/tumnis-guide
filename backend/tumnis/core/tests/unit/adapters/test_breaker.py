"""Circuit breaker: the pure transition function and the stateful wrapper (P0-09).

Imports of the code under test sit inside the tests so a missing module fails the spec
test instead of erroring the whole file at collection.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
)

from tumnis.core.clock import FixedClock

if TYPE_CHECKING:
    from tumnis.core.adapters.breaker import BreakerConfig, BreakerState, CircuitBreaker

T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


def state_of(breaker: CircuitBreaker) -> str:
    """Read the state afresh (a bare `breaker.state` stays narrowed for mypy across calls)."""
    return breaker.state


class BreakerMachine(RuleBasedStateMachine):
    """A real CircuitBreaker on a FixedClock against a model driven by `next_state`."""

    def __init__(self) -> None:
        super().__init__()
        self.clock = FixedClock(T0)
        self.cfg: BreakerConfig | None = None
        self.breaker: CircuitBreaker | None = None
        self.model: BreakerState | None = None
        self.in_flight = 0  # allowed requests that have not finished yet

    @initialize(
        threshold=st.integers(min_value=1, max_value=5),
        cooldown_s=st.floats(min_value=0.5, max_value=30.0),
        half_open_max_calls=st.integers(min_value=1, max_value=3),
    )
    def build(self, threshold: int, cooldown_s: float, half_open_max_calls: int) -> None:
        from tumnis.core.adapters.breaker import (  # noqa: PLC0415
            BreakerConfig,
            BreakerState,
            CircuitBreaker,
        )

        self.cfg = BreakerConfig(
            failure_threshold=threshold,
            cooldown_s=cooldown_s,
            half_open_max_calls=half_open_max_calls,
        )
        self.breaker = CircuitBreaker(self.cfg, self.clock)
        self.model = BreakerState()

    def _step(self, event: Any) -> bool:
        from tumnis.core.adapters.breaker import next_state  # noqa: PLC0415

        assert self.model is not None
        assert self.cfg is not None
        self.model, allowed = next_state(self.model, event, self.clock.now(), self.cfg)
        return allowed

    @rule()
    def request(self) -> None:
        from tumnis.core.adapters.base import CircuitOpen  # noqa: PLC0415

        assert self.breaker is not None
        allowed = self._step("request")
        try:
            self.breaker.before_call()
        except CircuitOpen:
            refused = True
        else:
            refused = False
        assert refused is not allowed
        if allowed:
            self.in_flight += 1

    @precondition(lambda self: self.in_flight > 0)
    @rule()
    def succeed(self) -> None:
        assert self.breaker is not None
        self.in_flight -= 1
        self._step("success")
        self.breaker.on_success()

    @precondition(lambda self: self.in_flight > 0)
    @rule()
    def fail(self) -> None:
        assert self.breaker is not None
        self.in_flight -= 1
        self._step("failure")
        self.breaker.on_failure()

    @rule(seconds=st.floats(min_value=0, max_value=60))
    def advance(self, seconds: float) -> None:
        self.clock.advance(seconds=seconds)

    @precondition(lambda self: self.breaker is not None)
    @invariant()
    def breaker_matches_model(self) -> None:
        assert self.breaker is not None
        assert self.model is not None
        assert self.cfg is not None
        assert self.breaker.state == self.model.state
        if self.model.state == "closed":
            assert self.model.consecutive_failures < self.cfg.failure_threshold
            assert self.model.opened_at is None
        else:
            assert self.model.opened_at is not None
        assert 0 <= self.model.half_open_in_flight <= self.cfg.half_open_max_calls


BreakerMachine.TestCase.settings = settings(max_examples=100, stateful_step_count=40, deadline=None)


@pytest.mark.req("Architecture principle 5")
@pytest.mark.wp("P0-09")
class TestBreakerMachine(BreakerMachine.TestCase):  # type: ignore[misc,valid-type]
    """T-P0-09-03
    Hypothesis stateful test: CircuitBreaker matches a reference model over random request,
    success, failure and clock-advance steps.
    """


@pytest.mark.req("Architecture principle 5")
@pytest.mark.wp("P0-09")
@pytest.mark.xfail(strict=True, reason="spec:P0-09")
async def test_opens_after_n_failures_and_fails_fast(clock: FixedClock) -> None:
    """T-P0-09-04
    After failure_threshold failures fn is not called and CircuitOpen is raised.
    """
    from tumnis.core.adapters.base import (  # noqa: PLC0415
        Adapter,
        AdapterUnavailable,
        CallPolicy,
        CircuitOpen,
    )
    from tumnis.core.adapters.breaker import BreakerConfig  # noqa: PLC0415
    from tumnis.core.adapters.retry import RetryPolicy  # noqa: PLC0415

    class Demo(Adapter):
        name = "demo.breaker"

    policy = CallPolicy(
        timeout_s=1.0,
        retry=RetryPolicy(max_attempts=1),
        breaker=BreakerConfig(failure_threshold=3, cooldown_s=30.0),
    )
    adapter = Demo(policy=policy, clock=clock)
    calls = 0

    async def down() -> str:
        nonlocal calls
        calls += 1
        raise AdapterUnavailable("demo.breaker", "fetch", "connection refused", retryable=True)

    for _ in range(3):
        with pytest.raises(AdapterUnavailable):
            await adapter.call("fetch", down, idempotent=True)
    assert calls == 3

    with pytest.raises(CircuitOpen) as exc:
        await adapter.call("fetch", down, idempotent=True)
    assert calls == 3
    assert exc.value.retryable is False
    assert exc.value.adapter == "demo.breaker"
    assert exc.value.op == "fetch"


@pytest.mark.req("Architecture principle 5")
@pytest.mark.wp("P0-09")
def test_half_opens_after_cooldown_and_closes_on_success(clock: FixedClock) -> None:
    """T-P0-09-05
    At cooldown_s one trial is allowed; success closes; a second concurrent trial is refused.
    """
    from tumnis.core.adapters.base import CircuitOpen  # noqa: PLC0415
    from tumnis.core.adapters.breaker import BreakerConfig, CircuitBreaker  # noqa: PLC0415

    breaker = CircuitBreaker(BreakerConfig(failure_threshold=2, cooldown_s=30.0), clock)
    assert state_of(breaker) == "closed"
    for _ in range(2):
        breaker.before_call()
        breaker.on_failure()
    assert state_of(breaker) == "open"

    clock.advance(seconds=29.999)
    with pytest.raises(CircuitOpen):
        breaker.before_call()
    assert state_of(breaker) == "open"

    clock.advance(seconds=0.001)  # exactly cooldown_s after opening
    breaker.before_call()  # the one trial
    assert state_of(breaker) == "half_open"
    with pytest.raises(CircuitOpen):
        breaker.before_call()  # a second concurrent trial

    breaker.on_success()
    assert state_of(breaker) == "closed"
    for _ in range(3):
        breaker.before_call()
        breaker.on_success()
    assert state_of(breaker) == "closed"


@pytest.mark.req("Architecture principle 5")
@pytest.mark.wp("P0-09")
def test_half_open_failure_reopens_and_restarts_cooldown(clock: FixedClock) -> None:
    """T-P0-09-06
    Failure in half-open sets opened_at = now, so the cooldown starts again from the failure.
    """
    from tumnis.core.adapters.base import CircuitOpen  # noqa: PLC0415
    from tumnis.core.adapters.breaker import (  # noqa: PLC0415
        BreakerConfig,
        BreakerState,
        CircuitBreaker,
        next_state,
    )

    cfg = BreakerConfig(failure_threshold=1, cooldown_s=10.0)
    half_open = BreakerState(
        state="half_open",
        consecutive_failures=0,
        opened_at=T0 - timedelta(seconds=60),
        half_open_in_flight=1,
    )
    reopened, _ = next_state(half_open, "failure", T0, cfg)
    assert reopened.state == "open"
    assert reopened.opened_at == T0
    assert reopened.half_open_in_flight == 0

    breaker = CircuitBreaker(cfg, clock)
    breaker.before_call()
    breaker.on_failure()  # opens at T0
    clock.advance(seconds=10)
    breaker.before_call()  # half-open trial at T0+10
    clock.advance(seconds=4)
    breaker.on_failure()  # reopens at T0+14
    assert state_of(breaker) == "open"

    clock.advance(seconds=9.9)  # T0+23.9: the old cooldown has long passed, the new one not
    with pytest.raises(CircuitOpen):
        breaker.before_call()
    clock.advance(seconds=0.1)  # T0+24
    breaker.before_call()
    assert state_of(breaker) == "half_open"
