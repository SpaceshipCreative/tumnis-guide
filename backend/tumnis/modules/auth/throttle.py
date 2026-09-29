"""Sign-in failure counters in `auth_throttle` (P0-13, SEC-1, SEC-5).

One attempt locks the counters it touches (`app.auth_throttle_lock`, rows created at zero
when missing) for the length of its own transaction, so concurrent attempts on one email,
address or user queue up instead of undercounting. The arithmetic is the pure
`auth.rules` (`lockout_state`, `after_failure`); `app.auth_throttle_put` writes the result,
and a count of zero with no lock deletes the row.

Keys are sha256 of `login:<email>`, `ip:<address>` or `totp:<user_id>`: the table holds
no address or email in clear.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Final, Self

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import db
from tumnis.modules.auth import rules
from tumnis.modules.auth.rules import ThrottlePolicy

_LOCK: Final = text(
    "SELECT key_hash, failures, window_started_at, locked_until"
    " FROM app.auth_throttle_lock(CAST(:keys AS bytea[]), :now)"
)
_PUT: Final = text("SELECT app.auth_throttle_put(:key, :failures, :window, :locked)")


class LockedOut(Exception):  # noqa: N818  # the plan's name
    """A counter of this attempt is locked; retry after `retry_after_s`."""

    def __init__(self, retry_after_s: int) -> None:
        super().__init__(f"locked for {retry_after_s} s")
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class Key:
    name: str  # "login", "ip" or "totp": what is counted
    hash: bytes
    policy: ThrottlePolicy


def key(kind: str, value: str, policy: ThrottlePolicy) -> Key:
    return Key(kind, hashlib.sha256(f"{kind}:{value}".encode()).digest(), policy)


def email_key(email: str) -> Key:
    return key("login", email.strip().lower(), rules.PASSWORD_EMAIL)


def address_key(address: str | None) -> Key:
    return key("ip", address or "unknown", rules.ADDRESS)


def totp_key(user_id: object) -> Key:
    return key("totp", str(user_id), rules.TOTP_USER)


class Attempt:
    """`async with Attempt(keys, now) as attempt:` raises LockedOut on entry when any key
    is locked; inside, call `failed()` or `succeeded(*keys_to_reset)`; the counters are
    written when the block ends without an unexpected error."""

    def __init__(self, keys: list[Key], now: datetime) -> None:
        self.keys = keys
        self.now = now
        self.locked_now: list[Key] = []  # keys this failure locked
        self._rows: dict[bytes, rules.Throttle] = {}
        self._outcome: tuple[str, tuple[Key, ...]] | None = None
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> Self:
        session = db.app_sessionmaker()()
        await session.begin()
        self._session = session
        try:
            params = {"keys": [k.hash for k in self.keys], "now": self.now}
            for row in (await session.execute(_LOCK, params)).all():
                self._rows[bytes(row.key_hash)] = rules.Throttle(
                    row.failures, row.window_started_at, row.locked_until
                )
            waits = [
                rules.lockout_state(
                    state.failures,
                    state.window_started_at,
                    self.now,
                    locked_until=state.locked_until,
                    policy=k.policy,
                ).retry_after_s
                for k in self.keys
                if (state := self._rows.get(k.hash)) is not None
            ]
        except BaseException:
            await self._close(commit=False)
            raise
        if any(waits):
            await self._close(commit=True)
            raise LockedOut(max(waits))
        return self

    def failed(self) -> None:
        self._outcome = ("failed", tuple(self.keys))
        self.locked_now = []
        for k in self.keys:
            state = self._rows.get(k.hash)
            before = state.failures if state else 0
            window = state.window_started_at if state else None
            after = rules.after_failure(before, window, self.now, policy=k.policy)
            self._rows[k.hash] = after
            if after.locked_until is not None:
                self.locked_now.append(k)

    def succeeded(self, *reset: Key) -> None:
        self._outcome = ("succeeded", reset)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc is not None and self._outcome is None:
            await self._close(commit=False)
            return
        session = self._session
        assert session is not None  # noqa: S101  # set in __aenter__
        try:
            if self._outcome is not None:
                kind, touched = self._outcome
                for k in touched:
                    state = self._rows[k.hash] if kind == "failed" else None
                    await session.execute(
                        _PUT,
                        {
                            "key": k.hash,
                            "failures": state.failures if state else 0,
                            "window": state.window_started_at if state else self.now,
                            "locked": state.locked_until if state else None,
                        },
                    )
        except BaseException:
            await self._close(commit=False)
            raise
        await self._close(commit=True)

    async def _close(self, *, commit: bool) -> None:
        session, self._session = self._session, None
        if session is None:
            return
        try:
            if commit:
                await session.commit()
            else:
                await session.rollback()
        finally:
            await session.close()
