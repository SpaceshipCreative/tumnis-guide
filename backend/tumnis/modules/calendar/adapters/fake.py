"""`FakeGoogleCalendar`: the Google Calendar API replayed from recordings (P1-09).
Interface stub until the implementation."""

from typing import Any

from pydantic import AwareDatetime

from tumnis.core.adapters.errors import AdapterError
from tumnis.modules.calendar.adapters.port import CalendarInfo, OAuthClient, TokenSet


class FakeGoogleCalendar:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.refreshed: list[str] = []

    def script_pages(self, calendar_id: str, responses: list[dict[str, Any]]) -> None:
        raise NotImplementedError

    def revoke(self, refresh_token: str) -> None:
        raise NotImplementedError

    def fail_events(self, calendar_id: str, error: AdapterError) -> None:
        raise NotImplementedError

    async def exchange_code(
        self, client: OAuthClient, *, code: str, code_verifier: str, redirect_uri: str
    ) -> TokenSet:
        raise NotImplementedError

    async def refresh(self, client: OAuthClient, *, refresh_token: str) -> TokenSet:
        raise NotImplementedError

    async def list_calendars(self, access_token: str) -> list[CalendarInfo]:
        raise NotImplementedError

    async def list_events(
        self,
        access_token: str,
        calendar_id: str,
        *,
        time_min: AwareDatetime,
        time_max: AwareDatetime,
        page_token: str | None,
    ) -> dict[str, Any]:
        raise NotImplementedError
