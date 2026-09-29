"""Google's HTTP answers replayed from the recordings (P1-09): the real Google client and
connector run against them in the contract layer, with no socket opened.

`replay_transport()` is an `httpx.MockTransport` answering, by Host header and path:
- `POST oauth2.googleapis.com/token`: the recorded exchange for `code-a`, the recorded
  refresh, or the recorded `invalid_grant` for a refresh token in `revoked`;
- `GET www.googleapis.com/calendar/v3/users/me/calendarList`: account a's calendar list;
- `GET .../calendars/<id>/events`: the recorded page for (calendar id, pageToken).
Every request is kept in `requests` for assertions.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote

import httpx

from tumnis.core.adapters.registry import resolve
from tumnis.core.clock import FixedClock
from tumnis.modules.calendar.adapters.google import GoogleCalendarApi
from tumnis.modules.integrations.api import Connector

RECORDINGS = Path(__file__).resolve().parent / "recordings" / "google_calendar"
ACCOUNT_A = "avery@example.com"
T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
WINDOW = (datetime(2026, 3, 8, 5, 0, tzinfo=UTC), datetime(2026, 3, 24, 4, 0, tzinfo=UTC))
GOOGLE_ADDRESS = "142.250.0.10"  # a public address: the SSRF guard lets it through
EVENTS_PREFIX = "/calendar/v3/calendars/"


def _load(relative: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((RECORDINGS / relative).read_text())
    return data


class Replay:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.revoked: set[str] = set()
        self._pages = {
            (data["calendar_id"], data["request"]["pageToken"]): data["response"]
            for data in (
                _load(f"pages/{path.name}")
                for path in sorted((RECORDINGS / "pages").glob("account_*_page*.json"))
            )
        }

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.headers["host"], request.url.path
        if host == "oauth2.googleapis.com" and path == "/token":
            return self._token(parse_qs(request.content.decode()))
        if host == "www.googleapis.com" and path == "/calendar/v3/users/me/calendarList":
            return httpx.Response(200, json=_load("oauth/calendar_list_a.json")["response"])
        if host == "www.googleapis.com" and path.startswith(EVENTS_PREFIX):
            calendar_id = unquote(path.removeprefix(EVENTS_PREFIX).removesuffix("/events"))
            page = self._pages.get((calendar_id, request.url.params.get("pageToken")))
            if page is not None:
                return httpx.Response(200, json=page)
        return httpx.Response(404, json={"error": {"code": 404, "status": "NOT_FOUND"}})

    def _token(self, form: dict[str, list[str]]) -> httpx.Response:
        grant = form.get("grant_type", [""])[0]
        if grant == "authorization_code" and form.get("code") == ["code-a"]:
            return httpx.Response(200, json=_load("oauth/token_exchange.json")["response"])
        if grant == "refresh_token" and form.get("refresh_token", [""])[0] not in self.revoked:
            return httpx.Response(200, json=_load("oauth/token_refresh.json")["response"])
        recorded = _load("oauth/token_invalid_grant.json")
        return httpx.Response(recorded["status"], json=recorded["response"])


async def _google_address(host: str, port: int) -> list[str]:
    return [GOOGLE_ADDRESS]


def recorded_api(replay: Replay | None = None) -> GoogleCalendarApi:
    """The real Google client whose HTTP goes to the replay."""
    return GoogleCalendarApi(
        transport=(replay or Replay()).transport(),
        resolver=_google_address,
        clock=FixedClock(T0),
    )


def recorded_connector() -> Connector:
    """The registered real connector on the real client over the recorded responses."""
    import tumnis.wiring  # noqa: F401, PLC0415  # registers the connector

    connector: Connector = resolve(
        "integrations.connector.google_calendar",
        "real",
        api=recorded_api(),
        access_token="fake-access-a",
        calendar_ids=[ACCOUNT_A],
        self_email=ACCOUNT_A,
        window=WINDOW,
        clock=FixedClock(T0),
    )
    return connector
