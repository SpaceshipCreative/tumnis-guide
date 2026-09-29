"""Sign-in providers plug into the auth module (P0-13, Hosted readiness)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests._auth import SESSION_COOKIE, totp_code, totp_step

if TYPE_CHECKING:
    import httpx

    from tests._auth import Account
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("Hosted readiness")
@pytest.mark.wp("P0-13")
@pytest.mark.xfail(strict=True, reason="spec:P0-13")
async def test_fake_provider_registers_without_core_changes(
    client: httpx.AsyncClient, account: Account, clock: FixedClock
) -> None:
    """T-P0-13-16
    A test-only `FakeProvider` registered through `register_provider` signs in through
    `POST /v1/auth/login` with `provider: "fake"`: its credentials reach it, a wrong token
    is 401 `invalid_credentials`, the right one answers `step: totp`, and the TOTP step
    then sets the session. An unknown provider is 401 `invalid_credentials` too.
    """
    from tumnis.modules.auth.providers import (  # noqa: PLC0415
        register_provider,
        unregister_provider,
    )
    from tumnis.modules.auth.tests.fakes import FakeProvider  # noqa: PLC0415

    fake = FakeProvider(account.user_id, account.workspace_id)
    register_provider(fake)
    try:
        refused = await client.post("/v1/auth/login", json={"provider": "fake", "token": "no"})
        assert refused.status_code == 401, refused.text
        assert refused.json()["code"] == "invalid_credentials"

        clock.advance(seconds=5)
        first = await client.post("/v1/auth/login", json={"provider": "fake", "token": "let-me-in"})
        assert first.status_code == 200, first.text
        assert first.json()["step"] == "totp"
        assert fake.calls == [{"token": "no"}, {"token": "let-me-in"}]

        done = await totp_step(
            client, first.json()["preauth"], totp_code(account.totp_secret, clock.now())
        )
        assert done.status_code == 200, done.text
        assert client.cookies.get(SESSION_COOKIE)
    finally:
        unregister_provider("fake")

    clock.advance(seconds=5)
    unknown = await client.post("/v1/auth/login", json={"provider": "fake", "token": "let-me-in"})
    assert unknown.status_code == 401, unknown.text
    assert unknown.json()["code"] == "invalid_credentials"
