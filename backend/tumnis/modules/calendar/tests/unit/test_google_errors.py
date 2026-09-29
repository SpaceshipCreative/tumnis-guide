"""The Google client maps HTTP answers to the adapter errors (P1-09, AGENTS.md adapters):
5xx and 429 are retryable `AdapterUnavailable` (with Retry-After), `invalid_grant` is
`GrantRevoked`, other 4xx `AdapterRejected`; no token appears in an error."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from tumnis.core.adapters.errors import AdapterRejected, AdapterUnavailable
from tumnis.core.clock import FixedClock
from tumnis.modules.calendar.adapters.google import GoogleCalendarApi
from tumnis.modules.calendar.adapters.port import GrantRevoked, OAuthClient

T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
CLIENT = OAuthClient(client_id="client", client_secret=SecretStr("secret"))


def _api(response: httpx.Response) -> GoogleCalendarApi:
    async def resolver(host: str, port: int) -> list[str]:
        return ["142.250.0.10"]

    return GoogleCalendarApi(
        transport=httpx.MockTransport(lambda request: response),
        resolver=resolver,
        clock=FixedClock(T0),
    )


async def _events(api: GoogleCalendarApi) -> None:
    await api.list_events(
        "tok-secret", "cal@example.com", time_min=T0, time_max=T0, page_token=None
    )


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P1-09")
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_server_errors_are_unavailable(status: int) -> None:
    api = _api(
        httpx.Response(status, headers={"Retry-After": "7"}, json={"error": {"code": status}})
    )
    with pytest.raises(AdapterUnavailable) as caught:
        await _events(api)
    assert caught.value.retryable
    assert caught.value.retry_after_s == 7
    assert "tok-secret" not in str(caught.value)


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P1-09")
async def test_client_errors_are_rejected() -> None:
    api = _api(httpx.Response(403, json={"error": {"code": 403, "status": "PERMISSION_DENIED"}}))
    with pytest.raises(AdapterRejected) as caught:
        await _events(api)
    assert not isinstance(caught.value, GrantRevoked)
    assert "PERMISSION_DENIED" in str(caught.value)


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P1-09")
async def test_invalid_grant_is_grant_revoked() -> None:
    api = _api(httpx.Response(400, json={"error": "invalid_grant", "error_description": "x"}))
    with pytest.raises(GrantRevoked):
        await api.refresh(CLIENT, refresh_token="refresh-secret")


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P1-09")
async def test_token_expiry_counts_from_the_clock() -> None:
    api = _api(httpx.Response(200, json={"access_token": "a", "expires_in": 3600}))
    tokens = await api.refresh(CLIENT, refresh_token="r")
    assert tokens.expires_at == datetime(2026, 3, 9, 13, 0, tzinfo=UTC)
    assert tokens.refresh_token is None
