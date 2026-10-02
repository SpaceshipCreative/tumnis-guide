"""Writes in flight, for the test clock (A2.6, J8). Fakes only: `create_app` installs the
middleware only when the test routes are mounted, never in a real deployment.

A journey presses Start and then moves the server clock. The Start request reaches the api
first, but its route reads the clock only after authentication, idempotency and the
session, so a clock change that overtook it there stamped the write with the new time.
`POST /v1/test/clock` therefore first waits, at most `SETTLE_TIMEOUT_S`, for the writes
that were already being served when it arrived; a write still running after that reads
the new time, as before.
"""

import asyncio
import contextlib
import itertools
from typing import Final

from starlette.types import ASGIApp, Receive, Scope, Send

WRITE_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})
TEST_PREFIX: Final = "/v1/test/"  # the test routes themselves never wait on each other
SETTLE_TIMEOUT_S: Final = 2.0


class WritesInFlight:
    """The writes being served now, each with an event set when it ends."""

    def __init__(self) -> None:
        self._ids = itertools.count()
        self._open: dict[int, asyncio.Event] = {}

    def begin(self) -> int:
        token = next(self._ids)
        self._open[token] = asyncio.Event()
        return token

    def end(self, token: int) -> None:
        done = self._open.pop(token, None)
        if done is not None:
            done.set()

    async def settle(self) -> None:
        """Waits until the writes open now have ended, at most SETTLE_TIMEOUT_S."""
        pending = [done.wait() for done in self._open.values()]
        if not pending:
            return
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(SETTLE_TIMEOUT_S):
                await asyncio.gather(*pending)


class WritesInFlightMiddleware:
    """Pure ASGI: registers every HTTP write outside the test routes for its duration."""

    def __init__(self, app: ASGIApp, writes: WritesInFlight) -> None:
        self.app = app
        self.writes = writes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in WRITE_METHODS
            or scope["path"].startswith(TEST_PREFIX)
        ):
            await self.app(scope, receive, send)
            return
        token = self.writes.begin()
        try:
            await self.app(scope, receive, send)
        finally:
            self.writes.end(token)
