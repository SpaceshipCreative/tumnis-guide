"""Token buckets per principal and per source address (P0-10, SEC-5).

`TumnisRoute` checks the route's bucket (`RoutePolicy.rate_limit`) before the handler runs:
a signed-in principal draws from its own bucket, an anonymous caller from its address's
(`anonymous` in place of `default`). A limited request is 429 `rate_limited` with
`Retry-After: <ceil(seconds)>`. Time comes from the app's `Clock`, so tests move it (a fakes
stack's test clock aside: the buckets read its base, real time).

State is per process; hosted mode with more than one api replica moves the buckets to the
Redis cache backend (config only).

`SlidingWindows` (P3-02) holds outbound limits: at most `limit` starts in any `period`
seconds per key (a provider account, `provider:<name>:<account>`). The connector sync
asks it before each provider request and waits the seconds it answers.
"""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from tumnis.core.clock import Clock, OverridableClock


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
        # Request rates are wall-clock. A fakes stack's test clock (`POST /v1/test/clock`)
        # may be pinned or moved in steps; the buckets read its base, so a pinned instant
        # never stops the refill and moving it mints no tokens.
        self._clock = clock.base if isinstance(clock, OverridableClock) else clock
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


class SlidingWindows:
    """At most `limit` admissions in any `period_s` window per key (P3-02: a provider's
    documented request limit, so a token bucket's burst could overrun it). An admission
    is recorded when granted; a caller refused waits the seconds `admit` returns, then
    asks again. Per process, like the buckets."""

    def __init__(self) -> None:
        self._starts: dict[str, deque[datetime]] = {}

    def admit(self, key: str, limit: int, period_s: float, now: datetime) -> float | None:
        """None when admitted (the start is recorded at `now`), else the seconds until the
        oldest start in the window leaves it."""
        period = timedelta(seconds=period_s)
        starts = self._starts.setdefault(key, deque())
        while starts and starts[0] <= now - period:
            starts.popleft()
        if len(starts) < limit:
            starts.append(now)
            return None
        return max((starts[0] + period - now).total_seconds(), 0.001)
