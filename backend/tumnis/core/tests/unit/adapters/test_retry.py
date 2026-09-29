"""Bounded jittered retries: `delay_for` and the retry loop in `Adapter.call` (P0-09)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tumnis.core.clock import FixedClock


def _recording_sleep(sleeps: list[float]) -> Callable[[float], Awaitable[None]]:
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return sleep


@pytest.mark.req("Architecture principle 5")
@pytest.mark.wp("P0-09")
@settings(max_examples=300, deadline=None)
@given(
    base=st.floats(min_value=0.001, max_value=5.0),
    cap=st.floats(min_value=0.001, max_value=60.0),
    attempt=st.integers(min_value=1, max_value=30),
    draw=st.floats(min_value=0.0, max_value=1.0, exclude_max=True),
)
def test_backoff_is_exponential_with_jitter_and_cap(
    base: float, cap: float, attempt: int, draw: float
) -> None:
    """T-P0-09-07
    For any policy and rand draw, 0 <= d_n <= min(cap, base*2**(n-1)); with rand = 1 - 1e-9
    the sequence doubles until the cap.
    """
    from tumnis.core.adapters.retry import RetryPolicy, delay_for  # noqa: PLC0415

    policy = RetryPolicy(max_attempts=31, base_s=base, cap_s=cap)
    delay = delay_for(policy, attempt, lambda: draw)
    assert 0 <= delay <= min(cap, base * 2 ** (attempt - 1))

    near_one = 1 - 1e-9
    default = RetryPolicy()  # base 0.2 s, cap 5 s
    seq = [delay_for(default, n, lambda: near_one) for n in range(1, 9)]
    assert seq == pytest.approx([0.2, 0.4, 0.8, 1.6, 3.2, 5.0, 5.0, 5.0], rel=1e-6)


@pytest.mark.req("Architecture principle 5")
@pytest.mark.wp("P0-09")
@pytest.mark.xfail(strict=True, reason="spec:P0-09")
@pytest.mark.parametrize("max_attempts", [1, 2, 3, 5])
async def test_attempts_never_exceed_max(clock: FixedClock, max_attempts: int) -> None:
    """T-P0-09-08
    A permanently failing retryable call runs exactly max_attempts times, sleeping between
    attempts only.
    """
    from tumnis.core.adapters.base import Adapter, AdapterUnavailable, CallPolicy  # noqa: PLC0415
    from tumnis.core.adapters.breaker import BreakerConfig  # noqa: PLC0415
    from tumnis.core.adapters.retry import RetryPolicy  # noqa: PLC0415

    class Demo(Adapter):
        name = "demo.retry"

    sleeps: list[float] = []
    policy = CallPolicy(
        timeout_s=1.0,
        retry=RetryPolicy(max_attempts=max_attempts, base_s=0.2, cap_s=5.0),
        breaker=BreakerConfig(failure_threshold=100),
    )
    adapter = Demo(policy=policy, clock=clock, sleep=_recording_sleep(sleeps), rand=lambda: 0.5)
    calls = 0

    async def down() -> None:
        nonlocal calls
        calls += 1
        raise AdapterUnavailable("demo.retry", "fetch", "503", retryable=True)

    with pytest.raises(AdapterUnavailable):
        await adapter.call("fetch", down, idempotent=True)
    assert calls == max_attempts
    assert sleeps == pytest.approx([0.1 * 2**n for n in range(max_attempts - 1)])


@pytest.mark.req("Architecture principle 5")
@pytest.mark.wp("P0-09")
@pytest.mark.xfail(strict=True, reason="spec:P0-09")
async def test_non_idempotent_and_rejected_calls_are_not_retried(clock: FixedClock) -> None:
    """T-P0-09-09
    idempotent=False or AdapterRejected gives one attempt.
    """
    from tumnis.core.adapters.base import (  # noqa: PLC0415
        Adapter,
        AdapterRejected,
        AdapterUnavailable,
        CallPolicy,
    )

    class Demo(Adapter):
        name = "demo.once"

    sleeps: list[float] = []
    adapter = Demo(policy=CallPolicy(timeout_s=1.0), clock=clock, sleep=_recording_sleep(sleeps))
    calls = 0

    async def down() -> None:
        nonlocal calls
        calls += 1
        raise AdapterUnavailable("demo.once", "send", "503", retryable=True)

    async def bad_request() -> None:
        nonlocal calls
        calls += 1
        raise AdapterRejected("demo.once", "send", "422 validation", retryable=False)

    with pytest.raises(AdapterUnavailable):
        await adapter.call("send", down, idempotent=False)
    assert calls == 1

    calls = 0
    with pytest.raises(AdapterRejected):
        await adapter.call("send", bad_request, idempotent=True)
    assert calls == 1
    assert sleeps == []


@pytest.mark.req("Architecture principle 5")
@pytest.mark.wp("P0-09")
@pytest.mark.xfail(strict=True, reason="spec:P0-09")
async def test_retry_after_is_honored_and_capped(clock: FixedClock) -> None:
    """T-P0-09-10
    retry_after_s=2 gives a 2 s delay; retry_after_s=600 gives retry_after_cap_s; Adapter.call
    sleeps for exactly those delays.
    """
    from tumnis.core.adapters.base import Adapter, AdapterUnavailable, CallPolicy  # noqa: PLC0415
    from tumnis.core.adapters.retry import RetryPolicy, delay_for  # noqa: PLC0415

    policy = RetryPolicy()
    assert delay_for(policy, 1, lambda: 0.5, retry_after_s=2) == 2
    assert delay_for(policy, 1, lambda: 0.5, retry_after_s=600) == policy.retry_after_cap_s == 30.0

    class Demo(Adapter):
        name = "demo.throttled"

    sleeps: list[float] = []
    adapter = Demo(
        policy=CallPolicy(timeout_s=1.0, retry=RetryPolicy(max_attempts=3)),
        clock=clock,
        sleep=_recording_sleep(sleeps),
    )
    answers = iter([2.0, 600.0])

    async def throttled() -> str:
        retry_after = next(answers, None)
        if retry_after is None:
            return "ok"
        raise AdapterUnavailable(
            "demo.throttled", "list", "429", retryable=True, retry_after_s=retry_after
        )

    assert await adapter.call("list", throttled, idempotent=True) == "ok"
    assert sleeps == [2.0, 30.0]
