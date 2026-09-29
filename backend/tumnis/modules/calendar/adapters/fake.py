"""`FakeGoogleCalendar`: the Google Calendar API replayed from the recordings (P1-09).

events.list pages come from `tests/recordings/google_calendar/pages/account_*_page*.json`,
keyed by (calendar id, pageToken), whatever window is asked for. Two accounts exist:
code `code-a` or `code-b` exchanges to tokens `fake-access-<x>` / `fake-refresh-<x>` of
avery@example.com or blake@example.org, each with its primary calendar.

Scripting hooks: `script_pages` (the next sync's pages for a calendar), `revoke` (a refresh
token answers the recorded `invalid_grant`), `fail_events` (every events.list of a calendar
raises). `calls` records every call as (op, kwargs) without secrets; `refreshed` lists the
refresh tokens used.
"""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from pydantic import AwareDatetime

from tumnis.core.adapters.errors import AdapterError, AdapterRejected
from tumnis.core.adapters.registry import Health
from tumnis.core.clock import Clock, SystemClock
from tumnis.modules.calendar.adapters.port import (
    INVALID_GRANT,
    CalendarInfo,
    GrantRevoked,
    OAuthClient,
    TokenSet,
)

NAME = "calendar.google"
RECORDINGS = Path(__file__).resolve().parents[1] / "tests" / "recordings" / "google_calendar"
ACCOUNTS = {"a": "avery@example.com", "b": "blake@example.org"}
TIME_ZONE = "America/New_York"
TOKEN_LIFETIME = timedelta(hours=1)


def _recorded_pages() -> dict[tuple[str, str | None], dict[str, Any]]:
    pages: dict[tuple[str, str | None], dict[str, Any]] = {}
    for path in sorted((RECORDINGS / "pages").glob("account_*_page*.json")):
        data = json.loads(path.read_text())
        pages[data["calendar_id"], data["request"]["pageToken"]] = data["response"]
    return pages


def _invalid_grant_message() -> str:
    path = RECORDINGS / "oauth" / "token_invalid_grant.json"
    body: dict[str, Any] = json.loads(path.read_text())["response"] if path.exists() else {}
    return str(body.get("error", INVALID_GRANT))


class FakeGoogleCalendar:
    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()
        self._pages = _recorded_pages()
        self._scripted: dict[str, list[dict[str, Any]]] = {}
        self._failing: dict[str, AdapterError] = {}
        self._revoked: set[str] = set()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.refreshed: list[str] = []

    # --- Scripting --------------------------------------------------------------------------

    def script_pages(self, calendar_id: str, responses: list[dict[str, Any]]) -> None:
        """The calendar's next events.list answers, in order, instead of the recordings."""
        self._scripted[calendar_id] = list(responses)

    def revoke(self, refresh_token: str) -> None:
        """Refreshing with this token answers `invalid_grant` from now on."""
        self._revoked.add(refresh_token)

    def fail_events(self, calendar_id: str, error: AdapterError) -> None:
        """Every events.list of this calendar raises `error`."""
        self._failing[calendar_id] = error

    def health_state(self) -> Health:
        return "ok"

    # --- Port -------------------------------------------------------------------------------

    async def exchange_code(
        self, client: OAuthClient, *, code: str, code_verifier: str, redirect_uri: str
    ) -> TokenSet:
        self.calls.append(("exchange_code", {"redirect_uri": redirect_uri}))
        account = code.removeprefix("code-")
        if account not in ACCOUNTS or not code_verifier:
            raise AdapterRejected(NAME, "exchange_code", "400 invalid_grant")
        return self._tokens(account, refresh=True)

    async def refresh(self, client: OAuthClient, *, refresh_token: str) -> TokenSet:
        self.calls.append(("refresh", {}))
        self.refreshed.append(refresh_token)
        if refresh_token in self._revoked:
            raise GrantRevoked(NAME, "refresh", _invalid_grant_message())
        account = refresh_token.removeprefix("fake-refresh-")
        if account not in ACCOUNTS:
            raise GrantRevoked(NAME, "refresh", INVALID_GRANT)
        return self._tokens(account, refresh=False)

    async def list_calendars(self, access_token: str) -> list[CalendarInfo]:
        self.calls.append(("list_calendars", {}))
        email = ACCOUNTS.get(access_token.removeprefix("fake-access-"))
        if email is None:
            raise AdapterRejected(NAME, "list_calendars", "401 UNAUTHENTICATED")
        return [CalendarInfo(id=email, summary=email, primary=True, time_zone=TIME_ZONE)]

    async def list_events(
        self,
        access_token: str,
        calendar_id: str,
        *,
        time_min: AwareDatetime,
        time_max: AwareDatetime,
        page_token: str | None,
    ) -> dict[str, Any]:
        self.calls.append(("list_events", {"calendar_id": calendar_id, "page_token": page_token}))
        if calendar_id in self._failing:
            raise self._failing[calendar_id]
        scripted = self._scripted.get(calendar_id)
        if scripted:
            response = scripted.pop(0)
            if not scripted:
                del self._scripted[calendar_id]
            return _copy(response)
        recorded = self._pages.get((calendar_id, page_token))
        if recorded is None:
            raise AdapterRejected(NAME, "list_events", "404 NOT_FOUND")
        return _copy(recorded)

    def _tokens(self, account: str, *, refresh: bool) -> TokenSet:
        return TokenSet(
            access_token=f"fake-access-{account}",
            refresh_token=f"fake-refresh-{account}" if refresh else None,
            expires_at=self._clock.now() + TOKEN_LIFETIME,
            scope="https://www.googleapis.com/auth/calendar.events.readonly",
        )


def _copy(response: dict[str, Any]) -> dict[str, Any]:
    """A deep copy: callers may change what they get."""
    copied: dict[str, Any] = json.loads(json.dumps(response))
    return copied
