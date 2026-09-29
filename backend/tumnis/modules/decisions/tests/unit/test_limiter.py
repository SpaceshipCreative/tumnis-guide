"""The Jev request limiter (P1-01, FR-11.9, R-32): at most 1,200 provider calls in any
60-second window, per Jev credential."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

START = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
WINDOW = timedelta(seconds=60)
LIMIT = 1_200


async def _grants(offsets: list[float]) -> list[datetime]:
    from tumnis.core.clock import FixedClock  # noqa: PLC0415
    from tumnis.modules.decisions.limiter import SlidingWindowLimiter  # noqa: PLC0415

    clock = FixedClock(START)

    async def sleep(seconds: float) -> None:  # waiting moves the fake clock
        clock.advance(timedelta(seconds=seconds))

    limiter = SlidingWindowLimiter(limit=LIMIT, window_s=60, clock=clock, sleep=sleep)
    grants: list[datetime] = []
    for offset in offsets:
        arrival = START + timedelta(seconds=offset)
        if clock.now() < arrival:
            clock.set(arrival)
        await limiter.acquire()
        grants.append(clock.now())
    return grants


@pytest.mark.req("FR-11.9")
@pytest.mark.wp("P1-01")
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(st.floats(0, 600), max_size=5_000).map(sorted))
@example([0.0] * 3_000)
@example([n * 0.01 for n in range(4_000)])
def test_never_more_than_1200_in_any_60s_window(offsets: list[float]) -> None:
    """T-P1-01-11
    For sorted random arrival times on a FixedClock (waiting advances the clock), the
    grants in any window [t, t+60 s) starting at a grant number at most 1,200; every call
    is granted, in order, never before it arrived, and a burst is served at full rate.
    """
    grants = asyncio.run(_grants(offsets))
    assert len(grants) == len(offsets)
    assert grants == sorted(grants)
    for offset, granted in zip(offsets, grants, strict=True):
        assert granted >= START + timedelta(seconds=offset) - timedelta(microseconds=1)
    end = 0
    for start, t in enumerate(grants):
        end = max(end, start)
        while end < len(grants) and grants[end] < t + WINDOW:
            end += 1
        assert end - start <= LIMIT, (t, end - start)
    if len(offsets) > LIMIT and offsets[LIMIT] - offsets[0] < 1:
        assert grants[LIMIT - 1] - grants[0] < WINDOW  # the first 1,200 go without waiting


@pytest.mark.req("FR-11.9", "R-32")
@pytest.mark.wp("P1-01")
def test_one_limiter_per_credential_and_the_key_is_never_the_key() -> None:
    """limiter_for keys limiters on a credential fingerprint: the same key shares one, a
    different key gets its own; the fingerprint does not contain the key."""
    from tumnis.core.clock import FixedClock  # noqa: PLC0415
    from tumnis.modules.decisions.limiter import (  # noqa: PLC0415
        credential_fingerprint,
        limiter_for,
    )

    clock = FixedClock(START)
    key_a, key_b = "ts_live_aaaaaaaaaaaaaaaaaaaaaaaa", "ts_live_bbbbbbbbbbbbbbbbbbbbbbbb"
    fp_a = credential_fingerprint(key_a)
    assert key_a not in fp_a
    assert fp_a == credential_fingerprint(key_a)
    assert fp_a != credential_fingerprint(key_b)
    assert limiter_for(fp_a, clock=clock) is limiter_for(fp_a, clock=clock)
    assert limiter_for(fp_a, clock=clock) is not limiter_for(
        credential_fingerprint(key_b), clock=clock
    )
    assert limiter_for(fp_a, clock=clock).limit == LIMIT


@pytest.mark.req("FR-11.9")
@pytest.mark.wp("P1-01")
def test_lowering_rpm_keeps_the_window_history() -> None:
    """A changed `rpm` updates the credential's limiter in place: grants already taken in
    the window still count, so lowering the limit cannot open a burst over the new one."""
    from tumnis.core.clock import FixedClock  # noqa: PLC0415
    from tumnis.modules.decisions.limiter import (  # noqa: PLC0415
        _LIMITERS,
        credential_fingerprint,
        limiter_for,
    )

    clock = FixedClock(START)
    fp = credential_fingerprint("ts_live_rpm_change_cccccccccccccccc")

    async def run() -> None:
        limiter = limiter_for(fp, rpm=5, clock=clock)
        for _ in range(3):
            await limiter.acquire()
        clock.advance(timedelta(seconds=10))
        lowered = limiter_for(fp, rpm=2, clock=clock)
        assert lowered is limiter
        assert lowered.limit == 2
        with pytest.raises(TimeoutError):  # 3 grants in the window already: it must wait
            await asyncio.wait_for(lowered.acquire(), timeout=0.05)
        clock.advance(WINDOW)
        assert await asyncio.wait_for(lowered.acquire(), timeout=1) == clock.now()

    try:
        asyncio.run(run())
        with pytest.raises(ValueError, match="at least 1"):
            limiter_for(fp, rpm=0, clock=clock)
    finally:
        _LIMITERS.pop(fp, None)
