"""Sign-in: a password step that never yields a session, a TOTP step that does, and the
checks around them (P0-13, SEC-1, FR-9.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests._auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    TOTP_STEP,
    password_step,
    start_setup,
    totp_code,
    totp_step,
)

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from tests._auth import Account
    from tests._pg import DbUrls
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("SEC-1")
@pytest.mark.wp("P0-13")
@pytest.mark.xfail(strict=True, reason="spec:P0-13")
async def test_login_rehashes_when_parameters_change(
    client: httpx.AsyncClient, account: Account, db: DbUrls
) -> None:
    """T-P0-13-02
    A user whose stored hash was made with `time_cost=1`: after a successful password step,
    `users.password_hash` is a new `$argon2id$` hash with the current parameters that still
    verifies the same password.
    """
    import psycopg  # noqa: PLC0415
    from argon2 import PasswordHasher  # noqa: PLC0415

    from tests._pg import OWNER  # noqa: PLC0415
    from tumnis.modules.auth.passwords import HASHER  # noqa: PLC0415

    old = PasswordHasher(time_cost=1).hash(account.password)
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        conn.execute("UPDATE users SET password_hash = %s WHERE id = %s", (old, account.user_id))

    response = await password_step(client, account.email, account.password)
    assert response.status_code == 200, response.text

    with psycopg.connect(db.libpq(OWNER)) as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id = %s", (account.user_id,)
        ).fetchone()
    assert row is not None
    stored: str = row[0]
    assert stored != old
    assert stored.startswith("$argon2id$")
    assert not HASHER.check_needs_rehash(stored)
    assert HASHER.verify(stored, account.password)


def _session_routes(app: FastAPI) -> list[tuple[str, str]]:
    from tests.meta._csrf import session_routes  # noqa: PLC0415

    return [(method, path) for method, path, _route in session_routes(app)]


@pytest.mark.req("SEC-1", "FR-9.2")
@pytest.mark.wp("P0-13")
@pytest.mark.xfail(strict=True, reason="spec:P0-13")
async def test_password_alone_never_yields_a_session(
    app: FastAPI, client: httpx.AsyncClient, account: Account
) -> None:
    """T-P0-13-03
    The right password answers `{"step": "totp", "preauth": ...}` and sets no cookie; the
    preauth token, sent as the session cookie or as a bearer token, is 401 on every route
    whose policy takes a session.
    """
    from tests.meta._csrf import fill_path  # noqa: PLC0415

    response = await password_step(client, account.email, account.password)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["step"] == "totp"
    preauth = body["preauth"]
    assert preauth
    assert "set-cookie" not in response.headers
    assert SESSION_COOKIE not in client.cookies

    routes = _session_routes(app)
    assert ("GET", "/v1/auth/sessions") in routes
    for method, path in routes:
        url = fill_path(path)
        as_cookie = await client.request(
            method, url, headers={"Cookie": f"{SESSION_COOKIE}={preauth}"}
        )
        assert as_cookie.status_code == 401, (method, path, as_cookie.text)
        as_bearer = await client.request(
            method, url, headers={"Authorization": f"Bearer {preauth}"}
        )
        assert as_bearer.status_code == 401, (method, path, as_bearer.text)


@pytest.mark.req("SEC-1")
@pytest.mark.wp("P0-13")
@pytest.mark.xfail(strict=True, reason="spec:P0-13")
async def test_totp_completes_sign_in_and_rejects_replay(
    client: httpx.AsyncClient, account: Account, clock: FixedClock
) -> None:
    """T-P0-13-04
    The right code at the fixed clock sets the session and CSRF cookies and the session
    works; a wrong code is 401 `invalid_code`; the same right code again, in a new password
    step, is 401 `totp_replayed`.
    """
    code = totp_code(account.totp_secret, clock.now())
    first = await password_step(client, account.email, account.password)
    signed_in = await totp_step(client, first.json()["preauth"], code)
    assert signed_in.status_code == 200, signed_in.text
    assert client.cookies.get(SESSION_COOKIE)
    assert client.cookies.get(CSRF_COOKIE)
    assert (await client.get("/v1/auth/sessions")).status_code == 200
    client.cookies.clear()

    clock.advance(seconds=5)
    again = await password_step(client, account.email, account.password)
    wrong_code = "000000" if code != "000000" else "111111"
    wrong = await totp_step(client, again.json()["preauth"], wrong_code)
    assert wrong.status_code == 401, wrong.text
    assert wrong.json()["code"] == "invalid_code"
    assert SESSION_COOKIE not in client.cookies

    replay = await totp_step(client, again.json()["preauth"], code)
    assert replay.status_code == 401, replay.text
    assert replay.json()["code"] == "totp_replayed"
    assert SESSION_COOKIE not in client.cookies

    clock.advance(TOTP_STEP)
    fresh = await totp_step(
        client, again.json()["preauth"], totp_code(account.totp_secret, clock.now())
    )
    assert fresh.status_code == 200, fresh.text


@pytest.mark.req("SEC-1")
@pytest.mark.wp("P0-13")
@pytest.mark.xfail(strict=True, reason="spec:P0-13")
async def test_unknown_email_is_indistinguishable(
    client: httpx.AsyncClient, account: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-P0-13-08
    An unknown email gets the same status, body and problem code as a wrong password
    (401 `invalid_credentials`), and the hasher ran exactly once for each.
    """
    from tumnis.modules.auth import passwords  # noqa: PLC0415

    calls: list[str] = []
    real = passwords.HASHER

    class Spy:
        def __getattr__(self, name: str) -> Any:
            return getattr(real, name)

        def verify(self, stored: str, password: str) -> bool:
            calls.append(stored)
            return bool(real.verify(stored, password))

    monkeypatch.setattr(passwords, "HASHER", Spy())

    wrong = await password_step(client, account.email, "not-the-password")
    assert len(calls) == 1
    unknown = await password_step(client, "nobody@example.test", "not-the-password")
    assert len(calls) == 2

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["code"] == "invalid_credentials"
    assert wrong.headers["content-type"] == unknown.headers["content-type"]


@pytest.mark.req("SEC-1")
@pytest.mark.wp("P0-13")
@pytest.mark.xfail(strict=True, reason="spec:P0-13")
async def test_cross_origin_login_is_refused(
    client: httpx.AsyncClient, account: Account, clock: FixedClock
) -> None:
    """T-P0-13-12
    `Origin: https://evil.example` on login, TOTP and setup is 403 `bad_origin` before any
    credential is checked; the app's own origin passes.
    """
    evil = {"Origin": "https://evil.example"}
    login = await client.post(
        "/v1/auth/login",
        json={"email": account.email, "password": account.password},
        headers=evil,
    )
    assert login.status_code == 403, login.text
    assert login.json()["code"] == "bad_origin"

    first = await password_step(client, account.email, account.password)
    assert first.status_code == 200, first.text
    code = totp_code(account.totp_secret, clock.now())
    totp = await client.post(
        "/v1/auth/totp", json={"preauth": first.json()["preauth"], "code": code}, headers=evil
    )
    assert totp.status_code == 403, totp.text
    assert totp.json()["code"] == "bad_origin"

    setup = await client.post(
        "/v1/setup",
        json={"email": "x@example.test", "password": "long-enough-password", "timezone": "UTC"},
        headers=evil,
    )
    assert setup.status_code == 403, setup.text
    assert setup.json()["code"] == "bad_origin"

    same = await client.post(
        "/v1/auth/login",
        json={"email": account.email, "password": account.password},
        headers={"Origin": "https://test"},
    )
    assert same.status_code == 200, same.text
    # The refused TOTP attempt did not use the code.
    done = await totp_step(client, same.json()["preauth"], code)
    assert done.status_code == 200, done.text


@pytest.mark.req("SEC-1")
@pytest.mark.wp("P0-13")
@pytest.mark.xfail(strict=True, reason="spec:P0-13")
async def test_setup_after_sign_in_is_refused(client: httpx.AsyncClient, account: Account) -> None:
    """T-P0-13-24
    Once a user exists, `POST /v1/setup` is 409 `already_set_up` and creates no user.
    """
    response = await start_setup(client, email="intruder@example.test")
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "already_set_up"
