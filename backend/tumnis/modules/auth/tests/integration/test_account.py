"""The signed-in account and second-factor re-enrolment from Settings (P0-26, SEC-1)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

import pytest

from tests._auth import (
    TOTP_STEP,
    Account,
    password_step,
    secret_from_uri,
    session_client_for,
    totp_code,
    totp_step,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._pg import DbUrls
    from tests.fixtures import MasterKeyFile, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("SEC-1")
@pytest.mark.wp("P0-26")
@pytest.mark.xfail(strict=True, reason="spec:P0-26")
async def test_account_shows_email_and_second_factor(
    request: pytest.FixtureRequest, workspace: WorkspaceHandle, master_key_file: MasterKeyFile
) -> None:
    """T-P0-26-14
    `GET /v1/auth/account` answers the signed-in user's id and email, its second factor
    (`totp`) and when it was confirmed.
    """
    client = request.getfixturevalue("session_client")
    account: Account = client.account

    response = await client.get("/v1/auth/account")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user_id"] == str(account.user_id)
    assert body["email"] == workspace.email
    assert body["second_factor"] == "totp"
    assert body["totp_confirmed_at"] is not None


@pytest.mark.req("SEC-1", "SEC-3")
@pytest.mark.wp("P0-26")
@pytest.mark.xfail(strict=True, reason="spec:P0-26")
async def test_totp_reenrolment_needs_the_password_and_a_code_from_the_new_secret(
    request: pytest.FixtureRequest,
    app: FastAPI,
    db: DbUrls,
    clock: FixedClock,
    master_key_file: MasterKeyFile,
) -> None:
    """T-P0-26-15
    `POST /v1/auth/totp/enrol` with a wrong password is 401 `invalid_credentials`; with the
    right one it answers a new `otpauth_uri` and an `enrol_token`, and the old secret still
    signs in. `POST /v1/auth/totp/enrol/confirm` with a code from the old secret is 401
    `invalid_code`; with a code from the new one it is 204, the account's
    `totp_confirmed_at` is now, the old secret stops signing in and the new one works. The
    switch is audited as `auth.totp_reset` (`via: settings`) without the secret.
    """
    from tumnis.core.tests.integration._audit import owner_rows  # noqa: PLC0415

    client = request.getfixturevalue("session_client")
    account: Account = client.account

    wrong = await client.post("/v1/auth/totp/enrol", json={"password": "not-the-password"})
    assert wrong.status_code == 401, wrong.text
    assert wrong.json()["code"] == "invalid_credentials"

    started = await client.post("/v1/auth/totp/enrol", json={"password": account.password})
    assert started.status_code == 200, started.text
    new_secret = secret_from_uri(started.json()["otpauth_uri"])
    assert new_secret != account.totp_secret
    token = started.json()["enrol_token"]

    old_code = await client.post(
        "/v1/auth/totp/enrol/confirm",
        json={"enrol_token": token, "code": totp_code(account.totp_secret, clock.now())},
    )
    assert old_code.status_code == 401, old_code.text
    assert old_code.json()["code"] == "invalid_code"

    confirmed = await client.post(
        "/v1/auth/totp/enrol/confirm",
        json={"enrol_token": token, "code": totp_code(new_secret, clock.now())},
    )
    assert confirmed.status_code == 204, confirmed.text
    confirmed_at = (await client.get("/v1/auth/account")).json()["totp_confirmed_at"]
    assert datetime.fromisoformat(confirmed_at) == clock.now()

    clock.advance(TOTP_STEP)
    other = session_client_for(app)
    first = await password_step(other, account.email, account.password)
    assert first.status_code == 200, first.text
    stale = await totp_step(
        other, first.json()["preauth"], totp_code(account.totp_secret, clock.now())
    )
    assert stale.status_code == 401, stale.text
    second = await password_step(other, account.email, account.password)
    fresh = await totp_step(other, second.json()["preauth"], totp_code(new_secret, clock.now()))
    assert fresh.status_code == 200, fresh.text

    rows = owner_rows(db, "SELECT details FROM audit_log WHERE action = 'auth.totp_reset'")
    assert rows == [({"via": "settings"},)]
    stored = json.dumps(owner_rows(db, "SELECT details FROM audit_log"), default=str)
    assert new_secret not in stored
    assert token not in stored
