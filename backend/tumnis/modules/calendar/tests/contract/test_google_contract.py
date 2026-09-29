"""The Google Calendar API port's contract (P1-09): the fake and the real client over the
recorded HTTP answers behave the same (AGENTS.md: fakes obey the real contract)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from tumnis.core.adapters.contract import AdapterContract
from tumnis.core.adapters.errors import AdapterRejected
from tumnis.modules.calendar.adapters.fake import FakeGoogleCalendar
from tumnis.modules.calendar.adapters.port import GoogleCalendarPort, GrantRevoked, OAuthClient
from tumnis.modules.calendar.tests.replay import WINDOW, Replay, recorded_api

ACCOUNT_A = "avery@example.com"
CLIENT = OAuthClient(client_id="client-123.apps.example.com", client_secret=SecretStr("not-real"))
REDIRECT = "https://tumnis.example.com/v1/calendar/oauth/callback"
Revoke = Callable[[str], None]


class GoogleContract(AdapterContract[GoogleCalendarPort]):
    port, adapter_name = GoogleCalendarPort, "calendar.google"

    @pytest.fixture
    def revoke(self) -> Revoke:  # overridden per implementation
        raise NotImplementedError

    async def test_exchange_code_gives_both_tokens(self, subject: GoogleCalendarPort) -> None:
        tokens = await subject.exchange_code(
            CLIENT, code="code-a", code_verifier="v" * 64, redirect_uri=REDIRECT
        )
        assert tokens.access_token == "fake-access-a"
        assert tokens.refresh_token == "fake-refresh-a"
        assert tokens.expires_at > datetime(2026, 1, 1, tzinfo=UTC)

    async def test_refresh_gives_a_new_access_token(self, subject: GoogleCalendarPort) -> None:
        tokens = await subject.refresh(CLIENT, refresh_token="fake-refresh-b")
        assert tokens.access_token == "fake-access-b"

    async def test_revoked_refresh_raises_grant_revoked(
        self, subject: GoogleCalendarPort, revoke: Revoke
    ) -> None:
        revoke("fake-refresh-a")
        with pytest.raises(GrantRevoked) as caught:
            await subject.refresh(CLIENT, refresh_token="fake-refresh-a")
        assert not caught.value.retryable
        assert "fake-refresh-a" not in str(caught.value)

    async def test_calendar_list_holds_the_primary(self, subject: GoogleCalendarPort) -> None:
        calendars = await subject.list_calendars("fake-access-a")
        (primary,) = [c for c in calendars if c.primary]
        assert primary.id == ACCOUNT_A
        assert primary.time_zone == "America/New_York"

    async def test_events_pages_follow_page_tokens(self, subject: GoogleCalendarPort) -> None:
        tokens: list[str | None] = [None]
        ids: list[str] = []
        while True:
            page = await subject.list_events(
                "fake-access-a",
                ACCOUNT_A,
                time_min=WINDOW[0],
                time_max=WINDOW[1],
                page_token=tokens[-1],
            )
            ids += [item["id"] for item in page["items"]]
            assert page["timeZone"] == "America/New_York"
            if "nextPageToken" not in page:
                break
            tokens.append(page["nextPageToken"])
        assert tokens == [None, "a-p2", "a-p3"]
        assert "a-kickoff" in ids
        assert len(ids) == len(set(ids))

    async def test_unknown_calendar_is_rejected(self, subject: GoogleCalendarPort) -> None:
        with pytest.raises(AdapterRejected):
            await subject.list_events(
                "fake-access-a",
                "nobody@example.com",
                time_min=WINDOW[0],
                time_max=WINDOW[1],
                page_token=None,
            )


@pytest.mark.contract
@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P1-09")
class TestGoogleFake(GoogleContract):
    impl = "fake"

    @pytest.fixture
    def subject(self) -> FakeGoogleCalendar:
        return FakeGoogleCalendar()

    @pytest.fixture
    def revoke(self, subject: FakeGoogleCalendar) -> Revoke:
        return subject.revoke


@pytest.mark.contract
@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P1-09")
class TestGoogleRecorded(GoogleContract):
    impl = "recorded"

    @pytest.fixture
    def replay(self) -> Replay:
        return Replay()

    @pytest.fixture
    def subject(self, replay: Replay) -> GoogleCalendarPort:
        return recorded_api(replay)

    @pytest.fixture
    def revoke(self, replay: Replay) -> Revoke:
        return replay.revoked.add
