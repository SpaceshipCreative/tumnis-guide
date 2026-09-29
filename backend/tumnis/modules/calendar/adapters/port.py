"""The Google Calendar API port (P1-09): the four calls the calendar module makes to
Google, as typed values. `google.py` is the REST client, `fake.py` replays recordings.

Scopes are the read-only pair in `calendar.rules.READONLY_SCOPES` (Data flow rule 1):
nothing here writes to Google.
"""

from typing import Any, Protocol

from pydantic import AwareDatetime, BaseModel, SecretStr

from tumnis.core.adapters.errors import AdapterRejected

INVALID_GRANT = "invalid_grant"


class OAuthClient(BaseModel):
    """The workspace's Google OAuth client (Settings > Calendar)."""

    client_id: str
    client_secret: SecretStr


class TokenSet(BaseModel):
    access_token: str
    refresh_token: str | None = None  # Google sends one on the first consent (prompt=consent)
    expires_at: AwareDatetime
    scope: str = ""


class CalendarInfo(BaseModel):
    """One calendar in the account's calendar list."""

    id: str
    summary: str
    primary: bool = False
    time_zone: str | None = None


class GrantRevoked(AdapterRejected):
    """Google answered `invalid_grant` on a refresh: the user revoked access or the grant
    expired. The account needs a new consent (`needs_reauth`)."""

    def __init__(self, adapter: str, op: str, message: str = INVALID_GRANT) -> None:
        super().__init__(adapter, op, message)


class GoogleCalendarPort(Protocol):
    async def exchange_code(
        self, client: OAuthClient, *, code: str, code_verifier: str, redirect_uri: str
    ) -> TokenSet:
        """The authorization code (PKCE) for tokens."""
        ...

    async def refresh(self, client: OAuthClient, *, refresh_token: str) -> TokenSet:
        """A fresh access token; GrantRevoked on `invalid_grant`. Google may omit the
        refresh token here: the caller keeps the one it has."""
        ...

    async def list_calendars(self, access_token: str) -> list[CalendarInfo]:
        """Every calendar the account can read (calendarList.list, all pages)."""
        ...

    async def list_events(
        self,
        access_token: str,
        calendar_id: str,
        *,
        time_min: AwareDatetime,
        time_max: AwareDatetime,
        page_token: str | None,
    ) -> dict[str, Any]:
        """One events.list page as Google sent it (`items`, `nextPageToken`, `timeZone`),
        with singleEvents=true, showDeleted=true, maxResults=250 (plan default)."""
        ...
