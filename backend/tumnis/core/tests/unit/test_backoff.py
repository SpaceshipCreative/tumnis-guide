"""Full-jitter backoff bounds (P0-07, REL-3). `full_jitter` is shared with the adapter
retries (P0-09 landed it first, so this test is green from the start)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st


@pytest.mark.req("REL-3")
@pytest.mark.wp("P0-07")
@given(
    attempt=st.integers(min_value=1, max_value=64),
    base=st.floats(min_value=0.0, max_value=60.0, allow_nan=False),
    cap=st.floats(min_value=0.0, max_value=3600.0, allow_nan=False),
    draw=st.floats(min_value=0.0, max_value=1.0, exclude_max=True),
)
def test_full_jitter_bounds(attempt: int, base: float, cap: float, draw: float) -> None:
    """T-P0-07-15
    For every attempt, base, cap and rand in [0, 1): 0 <= d <= min(cap, base * 2**(n-1)).
    """
    from tumnis.core.backoff import full_jitter  # noqa: PLC0415

    delay = full_jitter(attempt, base=base, cap=cap, rand=lambda: draw)
    assert 0.0 <= delay <= min(cap, base * 2.0 ** (attempt - 1))
