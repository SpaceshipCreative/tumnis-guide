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


@pytest.mark.req("Data flow rule 5")
@pytest.mark.wp("P1-09")
@pytest.mark.xfail(strict=True, reason="review:P1-09 soft-deleted connections keep tokens")
async def test_soft_deleted_connection_hides_its_credentials(
    app_db: DbUrls, workspace: WorkspaceHandle, oauth_client: None
) -> None:
    """A soft-deleted connection's tokens are neither opened nor rewritten, and its status
    stays as it was; a reconnect (`upsert_connection`) brings the row back first."""
    from tumnis.core.versioning import NotFound  # noqa: PLC0415
    from tumnis.modules.integrations.api import (  # noqa: PLC0415
        get_credentials,
        put_credentials,
        set_connection_status,
    )

    connection = await connect(workspace.ctx, "a")
    all_rows(
        app_db,
        "UPDATE connections SET deleted_at = now() WHERE id = %s RETURNING id",
        connection,
    )

    assert await get_credentials(workspace.ctx, connection) is None
    with pytest.raises(NotFound):
        await put_credentials(workspace.ctx, connection, {"refresh_token": "other"})
    await set_connection_status(workspace.ctx, connection, "error", last_error="gone")
    (row,) = all_rows(
        app_db, "SELECT status, last_error FROM connections WHERE id = %s", connection
    )
    assert row == {"status": "ok", "last_error": None}


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


async def _callback(session_client: SessionClient, code: str) -> None:
    start = await session_client.get("/v1/calendar/oauth/start")
    assert start.status_code == 200, start.text
    state = parse_qs(urlsplit(start.json()["url"]).query)["state"][0]
    callback = await session_client.get(
        "/v1/calendar/oauth/callback", params={"state": state, "code": code}
    )
    assert callback.status_code == 302, callback.text


@pytest.mark.req("FR-14.4", "Data flow rule 5")
@pytest.mark.wp("P1-09")
async def test_exchange_not_repeated_when_a_later_step_fails(  # noqa: PLR0917
    session_client: SessionClient,
    dbos_client: DBOSClient,
    google: FakeGoogleCalendar,
    oauth_client: None,
    workspace: WorkspaceHandle,
    db: DbUrls,
) -> None:
    """The one-use code is exchanged once: when listing the calendars fails after the
    exchange, the retry lists them again without a second exchange, and the account
    connects. No recorded step output holds a token in plaintext."""
    from tumnis.core.adapters.errors import AdapterUnavailable  # noqa: PLC0415
    from tumnis.modules.calendar.api import list_accounts  # noqa: PLC0415

    google.fail_calendar_list(AdapterUnavailable("calendar.google", "list_calendars", "503"))
    await _callback(session_client, "code-a")

    (workflow,) = await wait_for_workflows(dbos_client, "calendar_oauth_exchange")
    assert workflow.status == "SUCCESS"
    ops = [call[0] for call in google.calls]
    assert ops.count("exchange_code") == 1
    assert ops.count("list_calendars") == 2
    (account,) = await list_accounts(workspace.ctx)
    assert account.status == "connected"
    steps = await dbos_client.list_workflow_steps_async(workflow.workflow_id)
    recorded = repr([step["output"] for step in steps]).encode()
    for secret in (refresh_token("a"), "fake-access-a"):
        assert secret.encode() not in recorded


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P1-09")
async def test_rejected_code_ends_exchange_and_consumes_grant(  # noqa: PLR0917
    session_client: SessionClient,
    dbos_client: DBOSClient,
    google: FakeGoogleCalendar,
    oauth_client: None,
    workspace: WorkspaceHandle,
    db: DbUrls,
) -> None:
    """Google refusing the code (`invalid_grant`) is final: the exchange is not retried,
    the workflow ends, no account connects and the grant is used up."""
    from tumnis.modules.calendar.api import list_accounts  # noqa: PLC0415

    await _callback(session_client, "code-unknown")

    (workflow,) = await wait_for_workflows(dbos_client, "calendar_oauth_exchange")
    assert workflow.status == "SUCCESS"
    assert [call[0] for call in google.calls].count("exchange_code") == 1
    assert await list_accounts(workspace.ctx) == []
    for pending in all_rows(db, "SELECT * FROM oauth_pending"):
        assert pending["deleted_at"] is not None
        assert pending["code_enc"] is None


async def _assert_exchange_ended(
    dbos_client: DBOSClient, google: FakeGoogleCalendar, workspace: WorkspaceHandle, db: DbUrls
) -> None:
    """The exchange workflow succeeded once, with one exchange and one calendar list (no
    retry), no account connected and the grant used up."""
    from tumnis.modules.calendar.api import list_accounts  # noqa: PLC0415

    (workflow,) = await wait_for_workflows(dbos_client, "calendar_oauth_exchange")
    assert workflow.status == "SUCCESS"
    ops = [call[0] for call in google.calls]
    assert ops.count("exchange_code") == 1
    assert ops.count("list_calendars") == 1
    assert await list_accounts(workspace.ctx) == []
    pending_rows = all_rows(db, "SELECT * FROM oauth_pending")
    assert pending_rows
    for pending in pending_rows:
        assert pending["deleted_at"] is not None
        assert pending["code_enc"] is None


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P1-09")
async def test_rejected_calendar_list_ends_exchange_and_consumes_grant(  # noqa: PLR0917
    session_client: SessionClient,
    dbos_client: DBOSClient,
    google: FakeGoogleCalendar,
    oauth_client: None,
    workspace: WorkspaceHandle,
    db: DbUrls,
) -> None:
    """Google refusing the calendar list (a permanent `AdapterRejected`) is final: the list
    is not retried, the workflow ends, no account connects and the grant is used up."""
    from tumnis.core.adapters.errors import AdapterRejected  # noqa: PLC0415

    google.fail_calendar_list(
        AdapterRejected("calendar.google", "list_calendars", "403 PERMISSION_DENIED"), times=3
    )
    await _callback(session_client, "code-a")

    await _assert_exchange_ended(dbos_client, google, workspace, db)


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P1-09")
async def test_no_primary_calendar_ends_exchange_and_consumes_grant(  # noqa: PLR0917
    session_client: SessionClient,
    dbos_client: DBOSClient,
    google: FakeGoogleCalendar,
    oauth_client: None,
    workspace: WorkspaceHandle,
    db: DbUrls,
) -> None:
    """A calendar list with no calendar marked primary is final: the workflow ends
    without retrying, no account connects and the grant is used up."""
    from tumnis.modules.calendar.adapters.port import CalendarInfo  # noqa: PLC0415

    shared = CalendarInfo(
        id="team@group.calendar.google.com", summary="Team", primary=False, time_zone="UTC"
    )
    for _ in range(3):
        google.script_calendar_list([shared])
    await _callback(session_client, "code-a")

    await _assert_exchange_ended(dbos_client, google, workspace, db)
