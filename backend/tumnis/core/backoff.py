"""Backoff delays shared by the event relay (P0-07) and adapter retries (P0-09). Pure."""

from collections.abc import Callable


def full_jitter(attempt: int, *, base: float, cap: float, rand: Callable[[], float]) -> float:
    """AWS 'full jitter': uniform in [0, min(cap, base * 2**(attempt-1))]."""
    return rand() * min(cap, base * 2.0 ** (attempt - 1))
