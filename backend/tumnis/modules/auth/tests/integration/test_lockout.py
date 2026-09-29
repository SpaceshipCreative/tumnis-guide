"""Failed passwords and codes count toward a temporary lockout per email, per address and
per user's second factor (P0-13, SEC-1, SEC-5).

The clock moves 5 s before every attempt, so P0-10's `login` bucket (0.2 per second,
burst 5) refills and never answers `rate_limited`: every 429 here must be `locked_out`.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from tests._auth import TOTP_STEP, password_step, totp_code, totp_step

if TYPE_CHECKING:
    import httpx

    from tests._auth import Account
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

GAP = timedelta(seconds=5)
LOCK = timedelta(minutes=15)


def _locked(response: httpx.Response, retry_after: int) -> None:
    assert response.status_code == 429, response.text
    assert response.json()["code"] == "locked_out"
    assert response.headers["Retry-After"] == str(retry_after)


@pytest.mark.req("SEC-1")
@pytest.mark.wp("P0-13")
async def test_failed_passwords_lock_email_then_unlock(
    client: httpx.AsyncClient, account: Account, clock: FixedClock
) -> None:
    """T-P0-13-05
    Five wrong passwords (401 each); the sixth attempt, even with the right password, is
    429 `locked_out` with `Retry-After` = seconds until the lock ends. Once the clock passes
    the 15 minutes the right password works, and that success resets the count: four more
    failures do not lock.
    """
    for _ in range(5):
        clock.advance(GAP)
        wrong = await password_step(client, account.email, "not-the-password")
        assert wrong.status_code == 401, wrong.text
    locked_at = clock.now()
    clock.advance(GAP)
    _locked(await password_step(client, account.email, account.password), 900 - 5)

    clock.set(locked_at + LOCK)
    ok = await password_step(client, account.email, account.password)
    assert ok.status_code == 200, ok.text

    for _ in range(4):
        clock.advance(GAP)
        assert (await password_step(client, account.email, "nope")).status_code == 401
    clock.advance(GAP)
    assert (await password_step(client, account.email, account.password)).status_code == 200


@pytest.mark.req("SEC-1")
@pytest.mark.wp("P0-13")
async def test_failed_totp_codes_lock_second_factor(
    client: httpx.AsyncClient, account: Account, clock: FixedClock
) -> None:
    """T-P0-13-06
    Five wrong codes (401 `invalid_code` each); the sixth TOTP attempt, even with the right
    code, is 429 `locked_out` for that user's second factor; after 15 minutes the right
    code signs in.
    """
    clock.advance(GAP)
    preauth = (await password_step(client, account.email, account.password)).json()["preauth"]
    right = totp_code(account.totp_secret, clock.now())
    wrong = "000000" if right != "000000" else "111111"
    for _ in range(5):
        clock.advance(GAP)
        failed = await totp_step(client, preauth, wrong)
        assert failed.status_code == 401, failed.text
        assert failed.json()["code"] == "invalid_code"
    locked_at = clock.now()
    clock.advance(GAP)
    _locked(await totp_step(client, preauth, totp_code(account.totp_secret, clock.now())), 895)

    clock.set(locked_at + LOCK + TOTP_STEP)
    preauth = (await password_step(client, account.email, account.password)).json()["preauth"]
    done = await totp_step(client, preauth, totp_code(account.totp_secret, clock.now()))
    assert done.status_code == 200, done.text


@pytest.mark.req("SEC-1", "SEC-5")
@pytest.mark.wp("P0-13")
async def test_address_lock_spans_emails(
    client: httpx.AsyncClient, account: Account, clock: FixedClock
) -> None:
    """T-P0-13-07
    Twenty failures across twenty different emails from one address (no email near its
    own limit): the 21st attempt, the real account with its right password, is 429
    `locked_out` (the address lock).
    """
    for i in range(20):
        clock.advance(GAP)
        failed = await password_step(client, f"guess-{i}@example.test", "not-the-password")
        assert failed.status_code == 401, failed.text
    clock.advance(GAP)
    _locked(await password_step(client, account.email, account.password), 895)
