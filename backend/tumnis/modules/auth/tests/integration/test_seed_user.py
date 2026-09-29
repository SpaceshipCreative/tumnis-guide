"""The seed set's user can sign in once the stack is reset (P0-13; e2e's signedInPage and
A0.1 rely on it)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests._auth import SESSION_COOKIE, password_step, seed_user, seed_user_totp, totp_step

if TYPE_CHECKING:
    import httpx

    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("SEC-1", "FR-9.2")
@pytest.mark.wp("P0-13")
async def test_seed_user_signs_in_after_reset(client: httpx.AsyncClient, clock: FixedClock) -> None:
    """T-P0-13-27
    `POST /v1/test/reset` (fakes) loads the seed workspace and its user; that user signs in
    with the seed password and the TOTP code of the seed secret, and a second reset lets
    the same code sign in again (a fresh user row, no used step).
    """
    email, password, _secret = seed_user()
    for _ in range(2):
        reset = await client.post("/v1/test/reset")
        assert reset.status_code == 204, reset.text
        client.cookies.clear()
        first = await password_step(client, email, password)
        assert first.status_code == 200, first.text
        done = await totp_step(client, first.json()["preauth"], seed_user_totp(clock))
        assert done.status_code == 200, done.text
        assert client.cookies.get(SESSION_COOKIE)
