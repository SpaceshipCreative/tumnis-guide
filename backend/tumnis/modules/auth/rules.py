"""auth pure rules: no I/O, `now` and `tz` passed in.

- Time zones: `is_iana_zone` (P0-08).
- Lockout (P0-13, SEC-1): failures count per key inside a window; reaching the limit
  locks the key until `locked_until`. Plan defaults: 5 failed passwords per email, 20
  failures per source address and 5 failed codes per user, each within 15 minutes, lock
  for 15 minutes. A success resets its key (the caller deletes the counter).
- Sessions: idle expiry 30 days after the last use, slid at most once an hour.
- TOTP: a code is accepted once, only when its step is after the last step used.
- Device labels: "<browser> on <system>" from the user agent.
"""

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

_ZONE = re.compile(r"^(UTC|[A-Z][A-Za-z_]+(?:/[A-Za-z0-9_+\-]+)+)$")


def is_iana_zone(name: str, available: frozenset[str]) -> bool:
    """True when `name` is in the tz database and is a region/city style name (or UTC).
    Legacy abbreviations such as EST or PST8PDT are rejected. `available` is
    `zoneinfo.available_timezones()` at the call site, which keeps this rule pure."""
    return name in available and _ZONE.fullmatch(name) is not None


# --- Lockout -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ThrottlePolicy:
    max_failures: int
    window: timedelta
    lock: timedelta


FIFTEEN_MINUTES: Final = timedelta(minutes=15)
PASSWORD_EMAIL: Final = ThrottlePolicy(5, FIFTEEN_MINUTES, FIFTEEN_MINUTES)
ADDRESS: Final = ThrottlePolicy(20, FIFTEEN_MINUTES, FIFTEEN_MINUTES)
TOTP_USER: Final = ThrottlePolicy(5, FIFTEEN_MINUTES, FIFTEEN_MINUTES)


@dataclass(frozen=True)
class Lockout:
    locked: bool
    retry_after_s: int  # whole seconds until the lock ends, rounded up; 0 when not locked
    failures: int  # failures still inside the window


@dataclass(frozen=True)
class Throttle:
    """A key's counter as stored in auth_throttle."""

    failures: int
    window_started_at: datetime
    locked_until: datetime | None


def _in_window(window_start: datetime | None, now: datetime, policy: ThrottlePolicy) -> bool:
    return window_start is not None and now < window_start + policy.window


def lockout_state(
    failures: int,
    window_start: datetime | None,
    now: datetime,
    *,
    locked_until: datetime | None = None,
    policy: ThrottlePolicy = PASSWORD_EMAIL,
) -> Lockout:
    """Whether a key is locked at `now` (until `locked_until`, exclusive) and how many
    failures still count (none once the window is over)."""
    counted = failures if _in_window(window_start, now, policy) else 0
    if locked_until is not None and now < locked_until:
        wait = math.ceil((locked_until - now).total_seconds())
        return Lockout(locked=True, retry_after_s=max(wait, 1), failures=counted)
    return Lockout(locked=False, retry_after_s=0, failures=counted)


def after_failure(
    failures: int,
    window_start: datetime | None,
    now: datetime,
    *,
    policy: ThrottlePolicy = PASSWORD_EMAIL,
) -> Throttle:
    """The counter after one more failure at `now`: a window that is over (or never
    started) restarts at 1; reaching the limit locks for `policy.lock` from now."""
    if not _in_window(window_start, now, policy) or window_start is None:
        failures, window_start = 0, now
    failures += 1
    locked_until = now + policy.lock if failures >= policy.max_failures else None
    return Throttle(failures, window_start, locked_until)


# --- Sessions ----------------------------------------------------------------------------

IDLE_TIMEOUT: Final = timedelta(days=30)
SLIDE_EVERY: Final = timedelta(hours=1)  # plan default: at most one write an hour


def session_expired(last_seen: datetime, now: datetime) -> bool:
    """Idle expiry: 30 days after the last use (`expires_at = last_seen_at + 30 days`)."""
    return now >= last_seen + IDLE_TIMEOUT


def should_slide(last_seen: datetime, now: datetime) -> bool:
    """Whether a use at `now` moves last_seen_at (and expires_at) forward."""
    return now - last_seen >= SLIDE_EVERY


# --- TOTP ------------------------------------------------------------------------------

TOTP_PERIOD_S: Final = 30


def totp_step(now: datetime) -> int:
    return int(now.timestamp()) // TOTP_PERIOD_S


def totp_step_ok(step: int, last_step: int, code_offset: int) -> bool:
    """A code that matched at `step + code_offset` is fresh only when that step is after
    the last step used (replay protection, SEC-1)."""
    return step + code_offset > last_step


# --- Device labels -----------------------------------------------------------------------

_BROWSERS: Final = (
    ("Edge", re.compile(r"\bEdg(?:e|A|iOS)?/")),
    ("Opera", re.compile(r"\bOPR/")),
    ("Firefox", re.compile(r"\b(?:Firefox|FxiOS)/")),
    ("Chrome", re.compile(r"\b(?:Chrome|CriOS)/")),
    ("Safari", re.compile(r"\bVersion/[\d.]+.*\bSafari/")),
)
_SYSTEMS: Final = (
    ("iPhone", re.compile(r"\biPhone\b")),
    ("iPad", re.compile(r"\biPad\b")),
    ("Android", re.compile(r"\bAndroid\b")),
    ("Windows", re.compile(r"\bWindows\b")),
    ("macOS", re.compile(r"\bMac OS X\b|\bMacintosh\b")),
    ("ChromeOS", re.compile(r"\bCrOS\b")),
    ("Linux", re.compile(r"\bLinux\b")),
)
UNKNOWN_DEVICE: Final = "Unknown device"


def device_label(user_agent: str | None) -> str:
    """ "<browser> on <system>" (for example "Safari on iPhone"), or "Unknown device"."""
    if not user_agent:
        return UNKNOWN_DEVICE
    browser = next((name for name, pattern in _BROWSERS if pattern.search(user_agent)), None)
    system = next((name for name, pattern in _SYSTEMS if pattern.search(user_agent)), None)
    if browser and system:
        return f"{browser} on {system}"
    return browser or system or UNKNOWN_DEVICE
