"""CSRF sweep: every `/v1` write that accepts a session needs the CSRF token, and bearer
keys never do (P0-13, SEC-1). Cases come from the live route inventory, so a new session
write is covered without anyone listing it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest

from tests._auth import BASE_URL, CSRF_HEADER, SESSION_COOKIE, TOTP_STEP, session_client_for
from tests.meta._csrf import case_ids, fill_path, find_route, inventory_app

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _cases(kind: str) -> list[str]:
    try:
        return case_ids(inventory_app(), kind)
    except Exception:  # before P0-10/P0-13 there is no inventory
        return ["<no route inventory>"]


class _Spy:
    def __init__(self) -> None:
        self.calls = 0

    def wrap(self, route: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        original = route.dependant.call

        async def spy(*args: Any, **kwargs: Any) -> Any:
            self.calls += 1
            return await original(*args, **kwargs)

        monkeypatch.setattr(route.dependant, "call", spy)


def _bare(app: FastAPI, session_cookie: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=BASE_URL,
        cookies={SESSION_COOKIE: session_cookie},
    )


@pytest.mark.req("SEC-1")
@pytest.mark.wp("P0-13")
@pytest.mark.parametrize("case", _cases("session"))
async def test_every_session_write_requires_csrf(
    case: str,
    app: FastAPI,
    session_client: SessionClient,
    clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P0-13-10
    For each `/v1` write whose policy accepts a session and keeps `csrf=True`: with the
    session cookie and (a) no `X-CSRF-Token`, (b) another session's token, the answer is
    403 `csrf_failed` and the endpoint never runs; (c) with the right token CSRF passes,
    whatever the route then answers.
    """
    from tests._auth import sign_in  # noqa: PLC0415

    method, path = case.split(" ", 1)
    route = find_route(app, method, path)
    spy = _Spy()
    spy.wrap(route, monkeypatch)
    url = fill_path(path)
    cookie = session_client.cookies.get(SESSION_COOKIE)
    assert cookie, "session_client is not signed in"
    assert session_client.account is not None

    clock.advance(TOTP_STEP)
    other = session_client_for(app)
    try:
        await sign_in(other, session_client.account, clock)
        other_token = other.csrf
    finally:
        await other.aclose()
    assert other_token
    assert other_token != session_client.csrf

    async with _bare(app, cookie) as bare:
        missing = await bare.request(method, url, json={}, headers={"Idempotency-Key": "k-1a"})
        wrong = await bare.request(
            method, url, json={}, headers={CSRF_HEADER: other_token, "Idempotency-Key": "k-1b"}
        )
    for response in (missing, wrong):
        assert response.status_code == 403, (case, response.text)
        assert response.json()["code"] == "csrf_failed"
    assert spy.calls == 0

    async with _bare(app, cookie) as bare:
        right = await bare.request(
            method,
            url,
            json={},
            headers={CSRF_HEADER: session_client.csrf or "", "Idempotency-Key": "k-1c"},
        )
    code = right.json().get("code") if "json" in right.headers.get("content-type", "") else None
    assert code != "csrf_failed", (case, right.text)
    assert right.status_code != 401, (case, right.text)


@pytest.mark.req("SEC-1")
@pytest.mark.wp("P0-13")
def test_csrf_exempt_writes_give_a_reason(capsys: pytest.CaptureFixture[str]) -> None:
    """T-P0-13-26
    Every write whose policy sets `csrf=False` carries `csrf_exempt_reason`; the list is
    printed. Login, TOTP and setup are among them (no session exists yet).
    """
    from tests.meta._csrf import csrf_exempt_routes  # noqa: PLC0415

    exempt = csrf_exempt_routes(inventory_app())
    with capsys.disabled():
        for method, path, reason in exempt:
            print(f"csrf exempt: {method} {path}: {reason}")
    assert all(reason for _, _, reason in exempt), exempt
    paths = {(method, path) for method, path, _ in exempt}
    assert {
        ("POST", "/v1/auth/login"),
        ("POST", "/v1/auth/totp"),
        ("POST", "/v1/setup"),
        ("POST", "/v1/setup/totp"),
    } <= paths


@pytest.mark.req("SEC-1")
@pytest.mark.wp("P0-13")
@pytest.mark.parametrize("case", _cases("key"))
async def test_api_key_requests_skip_csrf(
    case: str, app: FastAPI, request: pytest.FixtureRequest
) -> None:
    """T-P0-13-11
    The writes that accept an API key, called with a bearer key (P0-14's `key_client`) and
    no CSRF token, never answer `csrf_failed`.
    """
    method, path = case.split(" ", 1)
    find_route(app, method, path)
    key_client = request.getfixturevalue("key_client")
    client = await key_client(frozenset({"tasks:read", "tasks:write"}))
    response = await client.request(method, fill_path(path), json={})
    code = (
        response.json().get("code") if "json" in response.headers.get("content-type", "") else None
    )
    assert code != "csrf_failed", (case, response.text)
