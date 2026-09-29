"""Bounded, jittered retries (P0-09)."""

from collections.abc import Callable
from dataclasses import dataclass

from tumnis.core.backoff import full_jitter


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3  # plan default, including the first try
    base_s: float = 0.2  # plan default
    cap_s: float = 5.0  # plan default
    retry_after_cap_s: float = 30.0  # plan default: longest Retry-After we honor


def delay_for(
    policy: RetryPolicy,
    attempt: int,
    rand: Callable[[], float],
    retry_after_s: float | None = None,
) -> float:
    """Delay before attempt+1. Server Retry-After wins when given, capped at retry_after_cap_s.

    Adapters used inside DBOS workflow steps pass `RetryPolicy(max_attempts=1)` and let the
    workflow retry, so attempts never multiply across the two layers.
    """
    if retry_after_s is not None:
        return max(0.0, min(retry_after_s, policy.retry_after_cap_s))
    return full_jitter(attempt, base=policy.base_s, cap=policy.cap_s, rand=rand)
