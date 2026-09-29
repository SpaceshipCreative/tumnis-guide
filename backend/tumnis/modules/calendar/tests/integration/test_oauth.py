"""Google OAuth per account: tokens sealed at rest, and a callback that only writes and
enqueues while the worker makes the token exchange (P1-09, Data flow rule 5,
Architecture principle 3)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import pytest

from tumnis.modules.calendar.tests.integration._calendar import (
    ACCOUNTS,
    all_rows,
    connect,
    outbound_blocked,
    refresh_token,
    wait_for_workflows,
)

if TYPE_CHECKING:
    from dbos import DBOSClient

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.modules.calendar.adapters.fake import FakeGoogleCalendar

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _text(row: dict[str, Any]) -> bytes:
    """Every column of a row as bytes, for a plaintext search."""
    return b"\x00".join(
        value if isinstance(value, bytes) else str(value).encode() for value in row.values()
    )


@pytest.mark.req("Data flow rule 5")
@pytest.mark.wp("P1-09")
async def test_tokens_encrypted_at_rest(
    app_db: DbUrls, workspace: WorkspaceHandle, oauth_client: None
) -> None:
    """T-P1-09-06
    After an account connects, its `connections` row holds the tokens only sealed: no
    column carries the refresh or access token in plaintext, and the calendar api opens
    them again with the workspace key.
    """
    from tumnis.modules.integrations.api import get_credentials  # noqa: PLC0415

    connection = await connect(workspace.ctx, "a")
    (row,) = all_rows(app_db, "SELECT * FROM connections WHERE id = %s", connection)

    assert row["credentials_enc"]
    assert row["key_version"] is not None
    for secret in (refresh_token("a"), "fake-access-a"):
        assert secret.encode() not in _text(row)
    for account_row in all_rows(app_db, "SELECT * FROM calendar_accounts"):
        assert refresh_token("a").encode() not in _text(account_row)

    opened = await get_credentials(workspace.ctx, connection)
    assert opened is not None
    assert opened["refresh_token"] == refresh_token("a")
    assert opened["access_token"] == "fake-access-a"


@pytest.mark.req("Architecture principle 3")
@pytest.mark.wp("P1-09")
async def test_callback_makes_no_outbound_call(  # noqa: PLR0917
    session_client: SessionClient,
    dbos_client: DBOSClient,
    google: FakeGoogleCalendar,
    oauth_client: None,
    workspace: WorkspaceHandle,
    db: DbUrls,
) -> None:
    """T-P1-09-08
    `oauth/start` answers the consent URL. With every outbound socket blocked, the
    callback validates `state`, stores the code sealed and answers 302 to Settings; the
    exchange runs as the `calendar_oauth_exchange` workflow (the fake exchanges the code,
    lists calendars) and leaves the account connected. The same state cannot be used twice.
    """
    from tumnis.modules.calendar.api import list_accounts  # noqa: PLC0415

    start = await session_client.get("/v1/calendar/oauth/start")
    assert start.status_code == 200, start.text
    state = parse_qs(urlsplit(start.json()["url"]).query)["state"][0]

    with outbound_blocked():
        callback = await session_client.get(
            "/v1/calendar/oauth/callback", params={"state": state, "code": "code-a"}
        )
    assert callback.status_code == 302, callback.text
    assert callback.headers["location"] == "/settings/calendar?connecting=1"
    for pending in all_rows(db, "SELECT * FROM oauth_pending"):
        assert b"code-a" not in _text(pending)

    (workflow,) = await wait_for_workflows(dbos_client, "calendar_oauth_exchange")
    assert workflow.status == "SUCCESS"
    assert [call[0] for call in google.calls[:2]] == ["exchange_code", "list_calendars"]
    (account,) = await list_accounts(workspace.ctx)
    assert account.google_email == ACCOUNTS["a"]
    assert account.status == "connected"
    assert account.selected_calendar_ids == [ACCOUNTS["a"]]

    replay = await session_client.get(
        "/v1/calendar/oauth/callback", params={"state": state, "code": "code-a"}
    )
    assert replay.status_code == 400
    assert replay.json()["code"] == "oauth_state_invalid"
    assert len(dbos_client.list_workflows(name="calendar_oauth_exchange")) == 1
