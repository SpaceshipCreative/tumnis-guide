"""First-run setup: one owner, a TOTP secret shown once, confirmed before any sign-in
(P0-13, SEC-1, Hosted readiness)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from tests._auth import (
    TEST_PASSWORD,
    TOTP_STEP,
    password_step,
    secret_from_uri,
    start_setup,
    totp_code,
)

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import AppFactory, PepperFile
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("Hosted readiness")
@pytest.mark.wp("P0-13")
@pytest.mark.xfail(strict=True, reason="spec:P0-13")
async def test_hosted_mode_disables_local_signup(
    db: DbUrls, app_factory: AppFactory, pepper_file: PepperFile, client: httpx.AsyncClient
) -> None:
    """T-P0-13-17
    With `DEPLOYMENT_MODE=hosted`, `POST /v1/setup` is 403 `signup_disabled` and creates
    nothing; self-hosted, it is allowed once (201 with an `otpauth://` URI), then 409
    `already_set_up`.
    """
    from tumnis.core.tests.integration._audit import owner_rows  # noqa: PLC0415

    hosted = app_factory(
        deployment_mode="hosted",
        public_base_url="https://test",
        api_key_pepper_file=str(pepper_file.path),
    )
    transport = httpx.ASGITransport(app=hosted)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as on_hosted:
        refused = await start_setup(on_hosted)
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "signup_disabled"
    assert owner_rows(db, "SELECT count(*) FROM users") == [(0,)]

    first = await start_setup(client)
    assert first.status_code == 201, first.text
    assert first.json()["otpauth_uri"].startswith("otpauth://totp/")
    again = await start_setup(client, email="second@example.test")
    assert again.status_code == 409, again.text
    assert again.json()["code"] == "already_set_up"
    assert owner_rows(db, "SELECT count(*) FROM users") == [(1,)]


@pytest.mark.req("SEC-1")
@pytest.mark.wp("P0-13")
@pytest.mark.xfail(strict=True, reason="spec:P0-13")
async def test_setup_requires_totp_confirmation(
    client: httpx.AsyncClient, clock: FixedClock
) -> None:
    """T-P0-13-18
    After `POST /v1/setup` and before `POST /v1/setup/totp` succeeds, the right password
    gets 403 `setup_incomplete` and no cookie; a wrong confirmation code is 401
    `invalid_code`; the right one confirms (and signs in), after which the password step
    answers `step: totp`.
    """
    started = await start_setup(client, email="owner@example.test")
    assert started.status_code == 201, started.text
    body = started.json()
    secret = secret_from_uri(body["otpauth_uri"])

    early = await password_step(client, "owner@example.test", TEST_PASSWORD)
    assert early.status_code == 403, early.text
    assert early.json()["code"] == "setup_incomplete"
    assert "set-cookie" not in early.headers

    wrong = await client.post(
        "/v1/setup/totp", json={"setup_token": body["setup_token"], "code": "000000"}
    )
    if totp_code(secret, clock.now()) == "000000":  # pragma: no cover  # one in a million
        pytest.skip("the right code happens to be 000000")
    assert wrong.status_code == 401, wrong.text
    assert wrong.json()["code"] == "invalid_code"

    confirmed = await client.post(
        "/v1/setup/totp",
        json={"setup_token": body["setup_token"], "code": totp_code(secret, clock.now())},
    )
    assert confirmed.status_code == 200, confirmed.text
    client.cookies.clear()
    clock.advance(TOTP_STEP)

    ready = await password_step(client, "owner@example.test", TEST_PASSWORD)
    assert ready.status_code == 200, ready.text
    assert ready.json()["step"] == "totp"


@pytest.mark.req("SEC-3")
@pytest.mark.wp("P0-13")
@pytest.mark.xfail(strict=True, reason="spec:P0-13")
async def test_setup_completed_is_audited(
    client: httpx.AsyncClient, clock: FixedClock, db: DbUrls
) -> None:
    """T-P0-13-23
    Confirming setup writes one `setup.completed` audit row in the new workspace, by the
    new user, at the clock's time; the password and the TOTP secret appear nowhere in it.
    """
    from tests._auth import run_setup  # noqa: PLC0415
    from tumnis.core.tests.integration._audit import owner_rows  # noqa: PLC0415

    at = clock.now()
    account = await run_setup(client, clock)
    rows = owner_rows(
        db,
        "SELECT workspace_id, actor_type, actor_id, occurred_at, details::text FROM audit_log"
        " WHERE action = 'setup.completed'",
    )
    assert len(rows) == 1, rows
    workspace_id, actor_type, actor_id, occurred_at, details = rows[0]
    assert (workspace_id, actor_type, actor_id) == (account.workspace_id, "user", account.user_id)
    assert occurred_at == at
    assert account.password not in details
    assert account.totp_secret not in details
