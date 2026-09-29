"""The per-call timeout in `Adapter.call` (P0-09).

T-P0-09-01 runs on real asyncio time: 25 examples, no deadline, so it stays under 5 s. If it
flakes on a loaded runner, move it to a virtual-time loop rather than widening the margin.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tumnis.core.clock import FixedClock

T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
MARGIN_S = 0.05  # plan default


@pytest.mark.req("Architecture principle 5")
@pytest.mark.wp("P0-09")
@settings(max_examples=25, deadline=None)
@given(timeout_s=st.floats(min_value=0.02, max_value=0.2))
def test_timeout_fires_inside_budget(timeout_s: float) -> None:
    """T-P0-09-01
    For timeout_s in [0.02, 0.2] and a call that sleeps three times longer, AdapterTimeout is
    raised (retryable) and the elapsed time is under timeout_s + 0.05.
    """
    from tumnis.core.adapters.base import (  # noqa: PLC0415
        Adapter,
        AdapterTimeout,
        CallPolicy,
    )
    from tumnis.core.adapters.retry import RetryPolicy  # noqa: PLC0415

    class Slow(Adapter):
        name = "demo.slow"

    async def run() -> tuple[float, AdapterTimeout]:
        adapter = Slow(
            policy=CallPolicy(timeout_s=timeout_s, retry=RetryPolicy(max_attempts=1)),
            clock=FixedClock(T0),
        )
        start = time.perf_counter()
        try:
            await adapter.call("wait", lambda: asyncio.sleep(timeout_s * 3), idempotent=True)
        except AdapterTimeout as exc:
            return time.perf_counter() - start, exc
        raise AssertionError("the call was not cut")

    elapsed, err = asyncio.run(run())
    assert elapsed < timeout_s + MARGIN_S
    assert err.retryable is True
    assert (err.adapter, err.op) == ("demo.slow", "wait")


@pytest.mark.req("Architecture principle 5")
@pytest.mark.wp("P0-09")
async def test_fast_call_is_not_cut(clock: FixedClock) -> None:
    """T-P0-09-02
    A call finishing well inside the budget returns its value.
    """
    from tumnis.core.adapters.base import Adapter, CallPolicy  # noqa: PLC0415

    class Quick(Adapter):
        name = "demo.quick"

    async def answer() -> int:
        await asyncio.sleep(0.001)
        return 42

    adapter = Quick(policy=CallPolicy(timeout_s=0.5), clock=clock)
    assert await adapter.call("answer", answer, idempotent=True) == 42
    assert adapter.health_state() == "ok"
