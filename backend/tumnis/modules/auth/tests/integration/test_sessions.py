"""Sessions: cookie flags, idle expiry with a sliding window, sign out here or everywhere
else (P0-13, SEC-1, R-18, R-21)."""

from __future__ import annotations

from datetime import timedelta
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING

import pytest

from tests._auth import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    TOTP_STEP,
    session_client_for,
    sign_in,
)

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from tests._auth import Account
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _set_cookies(response: httpx.Response) -> dict[str, dict[str, str | bool]]:
    """Set-Cookie headers as name -> attributes (lower-case names; flags as True)."""
    found: dict[str, dict[str, str | bool]] = {}
    for header in response.headers.get_list("set-cookie"):
        jar: SimpleCookie = SimpleCookie()
        jar.load(header)
        for name, morsel in jar.items():
            attrs: dict[str, str | bool] = {"value": morsel.value}
            for key in ("path", "samesite", "domain", "max-age"):
                if morsel[key]:
                    attrs[key] = str(morsel[key])
            for flag in ("httponly", "secure"):
                attrs[flag] = bool(morsel[flag])
            found[name] = attrs
    return found


@pytest.mark.req("SEC-1")
@pytest.mark.wp("P0-13")
async def test_session_cookie_flags(
    client: httpx.AsyncClient, account: Account, clock: FixedClock
) -> None:
    """T-P0-13-09
    `__Host-tumnis_session` is HttpOnly, Secure, SameSite=Lax, Path=/ with no Domain;
    `__Host-tumnis_csrf` is Secure, SameSite=Lax, Path=/, no Domain and not HttpOnly.
    """
    response = await sign_in(client, account, clock)
    cookies = _set_cookies(response)
    session = cookies[SESSION_COOKIE]
    assert session["httponly"] is True
    assert session["secure"] is True
    assert str(session["samesite"]).lower() == "lax"
    assert session["path"] == "/"
    assert "domain" not in session
    csrf = cookies[CSRF_COOKIE]
    assert csrf["httponly"] is False
    assert csrf["secure"] is True
    assert str(csrf["samesite"]).lower() == "lax"
    assert csrf["path"] == "/"
    assert "domain" not in csrf
    assert session["value"] != csrf["value"]


@pytest.mark.req("SEC-1")
@pytest.mark.wp("P0-13")
async def test_idle_session_ends_after_30_days(
    client: httpx.AsyncClient, account: Account, clock: FixedClock
) -> None:
    """T-P0-13-13
    A request at day 29 slides the expiry, so another 29 days later the session still
    works; 30 days and a second without use it is 401 `session_expired`.
    """
    await sign_in(client, account, clock)
    clock.advance(timedelta(days=29))
    assert (await client.get("/v1/auth/sessions")).status_code == 200
    clock.advance(timedelta(days=29))
    assert (await client.get("/v1/auth/sessions")).status_code == 200
    clock.advance(timedelta(days=30, seconds=1))
    expired = await client.get("/v1/auth/sessions")
    assert expired.status_code == 401, expired.text
    assert expired.json()["code"] == "session_expired"


@pytest.mark.req("SEC-1")
@pytest.mark.wp("P0-13")
async def test_sign_out_other_devices_keeps_this_one(
    app: FastAPI, account: Account, clock: FixedClock
) -> None:
    """T-P0-13-14
    Three sessions, each with its device label; the list shows all three with exactly one
    `current`. `DELETE /v1/auth/sessions` from one: 204, it still works and lists only
    itself, and the other two get 401.
    """
    agents = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Android 15; Mobile; rv:140.0) Gecko/140.0 Firefox/140.0",
    )
    clients = [session_client_for(app, **{"User-Agent": agent}) for agent in agents]
    try:
        for c in clients:
            await sign_in(c, account, clock)
            clock.advance(TOTP_STEP)
        here, *others = clients

        listed = await here.get("/v1/auth/sessions")
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"]
        assert len(items) == 3
        assert [item["current"] for item in items].count(True) == 1
        assert {item["device_label"] for item in items} == {
            "Chrome on macOS",
            "Safari on iPhone",
            "Firefox on Android",
        }

        revoked = await here.delete("/v1/auth/sessions")
        assert revoked.status_code == 204, revoked.text

        mine = await here.get("/v1/auth/sessions")
        assert mine.status_code == 200, mine.text
        assert [item["current"] for item in mine.json()["items"]] == [True]
        for other in others:
            assert (await other.get("/v1/auth/sessions")).status_code == 401
    finally:
        for c in clients:
            await c.aclose()


@pytest.mark.req("SEC-1")
@pytest.mark.wp("P0-13")
async def test_logout_revokes_current_session(
    app: FastAPI, account: Account, clock: FixedClock
) -> None:
    """T-P0-13-15
    `POST /v1/auth/logout` is 204 and clears both cookies; the old session cookie sent
    again is 401.
    """
    async with session_client_for(app) as c:
        await sign_in(c, account, clock)
        old = c.cookies.get(SESSION_COOKIE)
        assert old
        out = await c.post("/v1/auth/logout")
        assert out.status_code == 204, out.text
        cleared = _set_cookies(out)
        assert cleared[SESSION_COOKIE]["value"] == ""
        assert cleared[CSRF_COOKIE]["value"] == ""
        refused = await c.get("/v1/auth/sessions", headers={"Cookie": f"{SESSION_COOKIE}={old}"})
        assert refused.status_code == 401, refused.text


@pytest.mark.req("SEC-1")
@pytest.mark.wp("P0-13")
async def test_revoke_one_session_by_id(app: FastAPI, account: Account, clock: FixedClock) -> None:
    """T-P0-13-25
    `DELETE /v1/auth/sessions/{id}` revokes that session only (204, then 401 for it); an
    id that is not one of this user's sessions is 404 `not_found`; the CSRF header is
    required (403 `csrf_failed` without it).
    """
    import uuid  # noqa: PLC0415

    first, second = session_client_for(app), session_client_for(app)
    try:
        await sign_in(first, account, clock)
        clock.advance(TOTP_STEP)
        await sign_in(second, account, clock)
        items = (await first.get("/v1/auth/sessions")).json()["items"]
        target = next(item["id"] for item in items if not item["current"])

        no_csrf = await first.delete(f"/v1/auth/sessions/{target}", headers={CSRF_HEADER: "wrong"})
        assert no_csrf.status_code == 403, no_csrf.text
        assert no_csrf.json()["code"] == "csrf_failed"

        gone = await first.delete(f"/v1/auth/sessions/{target}")
        assert gone.status_code == 204, gone.text
        assert (await second.get("/v1/auth/sessions")).status_code == 401
        assert (await first.get("/v1/auth/sessions")).status_code == 200

        missing = await first.delete(f"/v1/auth/sessions/{uuid.uuid4()}")
        assert missing.status_code == 404, missing.text
        assert missing.json()["code"] == "not_found"
    finally:
        await first.aclose()
        await second.aclose()
