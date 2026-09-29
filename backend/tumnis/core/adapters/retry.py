"""Bounded, jittered retries (P0-09)."""

from collections.abc import Callable
from dataclasses import dataclass


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
    raise NotImplementedError
