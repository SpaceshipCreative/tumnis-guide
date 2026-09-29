"""Token buckets per principal and per source address (P0-10, SEC-5).

`TumnisRoute` checks the route's bucket (`RoutePolicy.rate_limit`) before the handler runs:
a signed-in principal draws from its own bucket, an anonymous caller from its address's
(`anonymous` in place of `default`). A limited request is 429 `rate_limited` with
`Retry-After: <ceil(seconds)>`. Time comes from the app's `Clock`, so tests move it.

State is per process; hosted mode with more than one api replica moves the buckets to the
Redis cache backend (config only).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from tumnis.core.clock import Clock


@dataclass(frozen=True)
class Bucket:
    rate_per_s: float
    burst: int


BUCKETS: Final[Mapping[str, Bucket]] = {
    "default": Bucket(rate_per_s=10, burst=50),  # plan default, per principal
    "anonymous": Bucket(rate_per_s=2, burst=10),  # plan default, per source address
    "login": Bucket(rate_per_s=0.2, burst=5),  # plan default; P0-13 adds lockout on top
}
MAX_SUBJECTS: Final = 10_000  # buckets kept before full ones are forgotten


class RateLimiter:
    def __init__(self, clock: Clock, buckets: Mapping[str, Bucket] = BUCKETS) -> None:
        self._clock = clock
        self._buckets = buckets
        self._state: dict[tuple[str, str], tuple[float, datetime]] = {}

    def check(self, bucket: str, subject: str) -> float | None:
        """Take one token: None when allowed, else the seconds until one is free."""
        spec = self._buckets[bucket]
        now = self._clock.now()
        key = (bucket, subject)
        tokens, at = self._state.get(key, (float(spec.burst), now))
        elapsed = max(0.0, (now - at).total_seconds())
        tokens = min(float(spec.burst), tokens + elapsed * spec.rate_per_s)
        if tokens >= 1:
            self._state[key] = (tokens - 1, now)
            self._prune()
            return None
        self._state[key] = (tokens, now)
        return (1 - tokens) / spec.rate_per_s

    def _prune(self) -> None:
        if len(self._state) <= MAX_SUBJECTS:
            return
        now = self._clock.now()
        for key, (tokens, at) in list(self._state.items()):
            spec = self._buckets[key[0]]
            if tokens + (now - at).total_seconds() * spec.rate_per_s >= spec.burst:
                del self._state[key]
