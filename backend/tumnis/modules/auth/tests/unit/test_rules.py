"""auth pure rules: lockout arithmetic, idle sessions, TOTP step replay (P0-13, SEC-1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
S = timedelta(seconds=1)
M = timedelta(minutes=1)


@pytest.mark.req("SEC-1")
@pytest.mark.wp("P0-13")
@pytest.mark.xfail(strict=True, reason="spec:P0-13")
def test_lockout_and_idle_rules() -> None:
    """T-P0-13-21
    `lockout_state(failures, window_start, now)` and `session_expired(last_seen, now)` at
    their boundaries (plan defaults: 5 passwords per email, 20 per address, 5 codes per
    user, each in 15 minutes, locking for 15 minutes; 30 days idle, slid at most hourly);
    `after_failure` counts, restarts the window and locks; `totp_step_ok` refuses a code
    whose step is not after the last one used.
    """
    from tumnis.modules.auth.rules import (  # noqa: PLC0415
        ADDRESS,
        PASSWORD_EMAIL,
        TOTP_USER,
        Throttle,
        after_failure,
        lockout_state,
        session_expired,
        should_slide,
        totp_step_ok,
    )

    assert (PASSWORD_EMAIL.max_failures, ADDRESS.max_failures, TOTP_USER.max_failures) == (5, 20, 5)
    for policy in (PASSWORD_EMAIL, ADDRESS, TOTP_USER):
        assert policy.window == policy.lock == 15 * M

    # Failures count inside the window only.
    state = lockout_state(4, T0, T0 + 15 * M - S)
    assert (state.locked, state.failures, state.retry_after_s) == (False, 4, 0)
    assert lockout_state(4, T0, T0 + 15 * M).failures == 0
    # A lock holds until locked_until (exclusive), Retry-After rounds up.
    locked = lockout_state(5, T0, T0 + M, locked_until=T0 + 16 * M)
    assert (locked.locked, locked.retry_after_s) == (True, 900)
    assert lockout_state(5, T0, T0 + 16 * M - S / 2, locked_until=T0 + 16 * M).retry_after_s == 1
    free = lockout_state(5, T0, T0 + 16 * M, locked_until=T0 + 16 * M)
    assert (free.locked, free.failures) == (False, 0)

    # The first failure opens a window; the fifth locks for 15 minutes from now.
    assert after_failure(0, None, T0) == Throttle(1, T0, None)
    assert after_failure(3, T0, T0 + M) == Throttle(4, T0, None)
    assert after_failure(4, T0, T0 + M) == Throttle(5, T0, T0 + 16 * M)
    assert after_failure(4, T0, T0 + 15 * M) == Throttle(1, T0 + 15 * M, None)
    assert after_failure(18, T0, T0 + M, policy=ADDRESS) == Throttle(19, T0, None)
    assert after_failure(19, T0, T0 + M, policy=ADDRESS) == Throttle(20, T0, T0 + 16 * M)
    assert after_failure(4, T0, T0 + M, policy=TOTP_USER).locked_until == T0 + 16 * M

    # Idle expiry: 30 days after the last use; the slide happens at most once an hour.
    assert not session_expired(T0, T0 + timedelta(days=30) - S)
    assert session_expired(T0, T0 + timedelta(days=30))
    assert session_expired(T0, T0 + timedelta(days=30) + S)
    assert not should_slide(T0, T0 + timedelta(hours=1) - S)
    assert should_slide(T0, T0 + timedelta(hours=1))

    # TOTP replay: the matched step must be after the last step used.
    assert totp_step_ok(100, 99, 0)
    assert not totp_step_ok(100, 100, 0)
    assert totp_step_ok(100, 100, 1)
    assert not totp_step_ok(100, 100, -1)
    assert not totp_step_ok(100, 99, -1)
    assert totp_step_ok(100, 0, -1)


@pytest.mark.req("SEC-1")
@pytest.mark.wp("P0-13")
@pytest.mark.xfail(strict=True, reason="spec:P0-13")
def test_device_label_names_browser_and_system() -> None:
    """T-P0-13-22
    The session's device label is "<browser> on <system>" from the user agent, and
    "Unknown device" when it says nothing recognisable.
    """
    from tumnis.modules.auth.rules import device_label  # noqa: PLC0415

    mac_chrome = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    )
    iphone_safari = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
    )
    android_firefox = "Mozilla/5.0 (Android 15; Mobile; rv:140.0) Gecko/140.0 Firefox/140.0"
    windows_edge = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0"
    )
    assert device_label(mac_chrome) == "Chrome on macOS"
    assert device_label(iphone_safari) == "Safari on iPhone"
    assert device_label(android_firefox) == "Firefox on Android"
    assert device_label(windows_edge) == "Edge on Windows"
    assert device_label("python-httpx/0.28.1") == "Unknown device"
    assert device_label(None) == "Unknown device"
