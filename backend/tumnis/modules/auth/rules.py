"""auth pure rules: no I/O, `now` and `tz` passed in."""

import re

_ZONE = re.compile(r"^(UTC|[A-Z][A-Za-z_]+(?:/[A-Za-z0-9_+\-]+)+)$")


def is_iana_zone(name: str, available: frozenset[str]) -> bool:
    """True when `name` is in the tz database and is a region/city style name (or UTC).
    Legacy abbreviations such as EST or PST8PDT are rejected. `available` is
    `zoneinfo.available_timezones()` at the call site, which keeps this rule pure."""
    return name in available and _ZONE.fullmatch(name) is not None


# --- P0-13 spec skeleton -----------------------------------------------------------------
from dataclasses import dataclass  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402


@dataclass(frozen=True)
class ThrottlePolicy:
    max_failures: int
    window: timedelta
    lock: timedelta


PASSWORD_EMAIL = ThrottlePolicy(5, timedelta(minutes=15), timedelta(minutes=15))
ADDRESS = ThrottlePolicy(20, timedelta(minutes=15), timedelta(minutes=15))
TOTP_USER = ThrottlePolicy(5, timedelta(minutes=15), timedelta(minutes=15))


@dataclass(frozen=True)
class Lockout:
    locked: bool
    retry_after_s: int
    failures: int


@dataclass(frozen=True)
class Throttle:
    failures: int
    window_started_at: datetime
    locked_until: datetime | None


def lockout_state(
    failures: int,
    window_start: datetime | None,
    now: datetime,
    *,
    locked_until: datetime | None = None,
    policy: ThrottlePolicy = PASSWORD_EMAIL,
) -> Lockout:
    raise NotImplementedError("P0-13")


def after_failure(
    failures: int,
    window_start: datetime | None,
    now: datetime,
    *,
    policy: ThrottlePolicy = PASSWORD_EMAIL,
) -> Throttle:
    raise NotImplementedError("P0-13")


def session_expired(last_seen: datetime, now: datetime) -> bool:
    raise NotImplementedError("P0-13")


def should_slide(last_seen: datetime, now: datetime) -> bool:
    raise NotImplementedError("P0-13")


def totp_step_ok(step: int, last_step: int, code_offset: int) -> bool:
    raise NotImplementedError("P0-13")


def device_label(user_agent: str | None) -> str:
    raise NotImplementedError("P0-13")
