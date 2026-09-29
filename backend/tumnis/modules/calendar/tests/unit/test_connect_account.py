"""An OAuth exchange connects a Google account only through its primary calendar
(P1-09, FR-14.4): the account's address and first selection come from the calendar
Google marks primary, never from a shared or holiday calendar listed first."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P1-09")
async def test_connect_refuses_account_without_primary_calendar() -> None:
    """A calendar list with no calendar marked primary is refused before anything is
    stored."""
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415
    from tumnis.modules.calendar.adapters.port import CalendarInfo, TokenSet  # noqa: PLC0415
    from tumnis.modules.calendar.api import connect_account  # noqa: PLC0415

    tokens = TokenSet(
        access_token="fake-access-a",
        refresh_token="fake-refresh-a",
        expires_at=datetime(2026, 3, 9, 13, 0, tzinfo=UTC),
        scope="https://www.googleapis.com/auth/calendar.events.readonly",
    )
    holidays = CalendarInfo(
        id="en.usa#holiday@group.v.calendar.example.com",
        summary="Holidays",
        primary=False,
        time_zone="America/New_York",
    )

    with pytest.raises(ValueError, match="primary"):
        await connect_account(WorkspaceContext(uuid.uuid4(), SYSTEM_ACTOR), tokens, [holidays])
