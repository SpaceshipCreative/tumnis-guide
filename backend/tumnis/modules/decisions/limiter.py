"""The Jev request limiter (P1-01, FR-11.9, R-32): a sliding window of provider calls per
Jev credential, 1,200 per 60 s by default (`decisions.jev.rpm` makes it config, since Jev's
limits "adjust dynamically").

`decisions.api.decide` (P1-02) acquires once per provider call, keyed on the credential's
fingerprint (never the key), so cache hits and fallback calls use no slot. `acquire` waits
until the call fits the window; the time comes from the injected `Clock` and the wait from
the injected `sleep`, so tests drive both with a FixedClock.
"""

import asyncio
import hashlib
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Final

from pydantic import SecretStr

from tumnis.core.clock import Clock, SystemClock

JEV_RPM_DEFAULT: Final = 1_200  # FR-11.9
WINDOW_S: Final = 60


class SlidingWindowLimiter:
    """At most `limit` grants in any window of `window_s` seconds."""

    def __init__(
        self,
        *,
        limit: int = JEV_RPM_DEFAULT,
        window_s: float = WINDOW_S,
        clock: Clock,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self.limit = limit
        self.window = timedelta(seconds=window_s)
        self._clock = clock
        self._sleep = sleep
        self._grants: deque[datetime] = deque()
        self._lock = asyncio.Lock()

    def _drop_expired(self, now: datetime) -> None:
        while self._grants and self._grants[0] + self.window <= now:
            self._grants.popleft()

    async def acquire(self) -> datetime:
        """Wait until one more call fits the window, take the slot, return the grant time."""
        async with self._lock:
            while True:
                now = self._clock.now()
                self._drop_expired(now)
                if len(self._grants) < self.limit:
                    self._grants.append(now)
                    return now
                await self._sleep((self._grants[0] + self.window - now).total_seconds())


def credential_fingerprint(api_key: SecretStr | str) -> str:
    """A stable, non-reversible name for a credential: the limiter's key and log field."""
    raw = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
    return hashlib.sha256(b"tumnis:jev-credential:" + raw.encode()).hexdigest()[:16]


_LIMITERS: dict[str, SlidingWindowLimiter] = {}


def limiter_for(
    fingerprint: str,
    *,
    rpm: int = JEV_RPM_DEFAULT,
    clock: Clock | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> SlidingWindowLimiter:
    """The one limiter for this credential in this process. A changed `rpm` updates its
    limit in place, keeping the grants already in the window, so lowering it cannot open a
    burst over the new limit. `clock` and `sleep` apply when the limiter is first made
    (tests make it first with a fixed clock and a sleep that advances it)."""
    limiter = _LIMITERS.get(fingerprint)
    if limiter is None:
        limiter = SlidingWindowLimiter(
            limit=rpm, clock=clock or SystemClock(), sleep=sleep or asyncio.sleep
        )
        _LIMITERS[fingerprint] = limiter
    elif limiter.limit != rpm:
        if rpm < 1:
            raise ValueError("limit must be at least 1")
        limiter.limit = rpm
    return limiter
