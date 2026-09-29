"""`GoogleCalendarApi`: the Google Calendar REST client (P1-09) over the core SSRF-guarded
HTTP client (`tumnis.core.net.guarded_client`).

- OAuth: the token endpoint for the code exchange (PKCE) and refreshes; `invalid_grant`
  on a refresh raises `GrantRevoked`.
- Reads: calendarList.list (every page) and one events.list page at a time
  ([events.list](https://developers.google.com/workspace/calendar/api/v3/reference/events/list)).

It runs inside DBOS steps, so it makes one attempt per call (`RetryPolicy(max_attempts=1)`)
and lets the workflow retry. 5xx and 429 are `AdapterUnavailable`, other 4xx
`AdapterRejected`. Tokens and codes never reach a log line or an error message.
"""

from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import AwareDatetime

from tumnis.core.adapters.base import Adapter, AdapterRejected, AdapterUnavailable, CallPolicy
from tumnis.core.adapters.retry import RetryPolicy
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.net import NetPolicy, Resolver, guarded_client, system_resolver
from tumnis.modules.calendar.adapters.port import (
    INVALID_GRANT,
    CalendarInfo,
    GrantRevoked,
    OAuthClient,
    TokenSet,
)

TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105  # an endpoint, not a secret
API_BASE = "https://www.googleapis.com/calendar/v3"
MAX_RESULTS = 250  # plan default
TIMEOUT_S = 15.0  # plan default for one Google call


class GoogleCalendarApi(Adapter):
    name = "calendar.google"

    def __init__(
        self,
        *,
        policy: NetPolicy | None = None,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
    ) -> None:
        """`policy` defaults to the hosted policy (public addresses only), which every
        Google endpoint meets; `transport` replaces the network (the recorded replay)."""
        self._clock = clock or SystemClock()
        super().__init__(
            policy=CallPolicy(timeout_s=TIMEOUT_S, retry=RetryPolicy(max_attempts=1)),
            clock=self._clock,
        )
        self._net = policy or NetPolicy(mode="hosted")
        self._resolver = resolver
        self._transport = transport

    # --- OAuth ------------------------------------------------------------------------------

    async def exchange_code(
        self, client: OAuthClient, *, code: str, code_verifier: str, redirect_uri: str
    ) -> TokenSet:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "client_id": client.client_id,
            "client_secret": client.client_secret.get_secret_value(),
        }
        return await self.call(
            "exchange_code", lambda: self._token("exchange_code", form), idempotent=False
        )

    async def refresh(self, client: OAuthClient, *, refresh_token: str) -> TokenSet:
        form = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client.client_id,
            "client_secret": client.client_secret.get_secret_value(),
        }
        return await self.call("refresh", lambda: self._token("refresh", form), idempotent=True)

    async def _token(self, op: str, form: Mapping[str, str]) -> TokenSet:
        now = self._clock.now()
        body = await self._send(op, "POST", TOKEN_URL, data=form)
        return TokenSet(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token"),
            expires_at=now + timedelta(seconds=int(body.get("expires_in", 3600))),
            scope=body.get("scope", ""),
        )

    # --- Reads ------------------------------------------------------------------------------

    async def list_calendars(self, access_token: str) -> list[CalendarInfo]:
        async def fetch() -> list[CalendarInfo]:
            out: list[CalendarInfo] = []
            token: str | None = None
            while True:
                params = {"pageToken": token} if token else {}
                body = await self._send(
                    "list_calendars",
                    "GET",
                    f"{API_BASE}/users/me/calendarList",
                    params=params,
                    access_token=access_token,
                )
                out += [
                    CalendarInfo(
                        id=item["id"],
                        summary=item.get("summaryOverride") or item.get("summary") or item["id"],
                        primary=bool(item.get("primary")),
                        time_zone=item.get("timeZone"),
                    )
                    for item in body.get("items", [])
                ]
                token = body.get("nextPageToken")
                if not token:
                    return out

        return await self.call("list_calendars", fetch, idempotent=True)

    async def list_events(
        self,
        access_token: str,
        calendar_id: str,
        *,
        time_min: AwareDatetime,
        time_max: AwareDatetime,
        page_token: str | None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {
            "singleEvents": "true",
            "showDeleted": "true",
            "maxResults": str(MAX_RESULTS),
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
        }
        if page_token:
            params["pageToken"] = page_token
        url = f"{API_BASE}/calendars/{quote(calendar_id, safe='@.')}/events"
        return await self.call(
            "list_events",
            lambda: self._send("list_events", "GET", url, params=params, access_token=access_token),
            idempotent=True,
        )

    # --- HTTP -------------------------------------------------------------------------------

    async def _send(
        self,
        op: str,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        data: Mapping[str, str] | None = None,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if access_token is not None:
            headers["Authorization"] = f"Bearer {access_token}"
        async with guarded_client(
            self._net, timeout=TIMEOUT_S, resolver=self._resolver, inner=self._transport
        ) as http:
            try:
                response = await http.request(
                    method, url, params=params, data=data, headers=headers
                )
            except httpx.TransportError as exc:
                raise AdapterUnavailable(self.name, op, type(exc).__name__) from None
        return self._body(op, response)

    def _body(self, op: str, response: httpx.Response) -> dict[str, Any]:
        status = response.status_code
        if status < 400:  # noqa: PLR2004
            body: dict[str, Any] = response.json()
            return body
        error = _error_code(response)
        if status == 429 or status >= 500:  # noqa: PLR2004
            retry_after = response.headers.get("Retry-After")
            raise AdapterUnavailable(
                self.name,
                op,
                f"{status} {error}",
                retry_after_s=float(retry_after) if retry_after and retry_after.isdigit() else None,
            )
        if error == INVALID_GRANT:
            raise GrantRevoked(self.name, op)
        raise AdapterRejected(self.name, op, f"{status} {error}")


def _error_code(response: httpx.Response) -> str:
    """Google's error code: `error` in an OAuth answer (a string) or `error.status` in an API
    answer; never the description, which may echo input."""
    try:
        body = response.json()
    except ValueError:
        return "error"
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        return str(error.get("status") or error.get("code") or "error")
    return "error"
