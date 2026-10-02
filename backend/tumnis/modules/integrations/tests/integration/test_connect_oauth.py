"""Connecting through OAuth (P3-02, FR-14.4): the `connect_oauth` workflow, the browser
callback, and token refresh that persists a rotated refresh token once, under a row lock."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import pytest

from tumnis.modules.integrations.tests.integration._connections import (
    connected,
    poll_authorize_url,
    rows,
    text_of,
    wait_for,
)

if TYPE_CHECKING:
    from dbos import DBOSClient

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import MasterKeyFile, WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.integrations.adapters.fake_oauth import FakeOAuthServer

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P3-02")
async def test_oauth_flow_completes_through_callback(  # noqa: PLR0917
    session_client: SessionClient,
    dbos_client: DBOSClient,
    workspace: WorkspaceHandle,
    oauth_server: FakeOAuthServer,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P3-02-03
    `POST /v1/connections` makes a `pending_auth` connection; `POST .../oauth/start`
    answers 202 with the `connect_oauth` workflow's id; polling `GET .../oauth/url`
    yields the fake server's authorize URL (PKCE S256, a state, the callback as redirect
    URI, the client registered dynamically). The fake approves it with code `c1`; the
    callback stores the code and redirects to the connection's page; the workflow
    exchanges the code and ends with the connection `ok`, its sealed credentials holding
    a token set with a refresh token. No `oauth_pending` row keeps the code in plaintext.
    """
    from tumnis.modules.integrations.api import ConnectionTokenStorage  # noqa: PLC0415

    made = await session_client.post(
        "/v1/connections", json={"provider": "fake", "account_label": "Work"}
    )
    assert made.status_code == 201, made.text
    connection = made.json()
    assert connection["status"] == "pending_auth"

    started = await session_client.post(f"/v1/connections/{connection['id']}/oauth/start")
    assert started.status_code == 202, started.text
    workflow_id = started.json()["workflow_id"]

    url = await poll_authorize_url(session_client, connection["id"])
    query = parse_qs(urlsplit(url).query)
    assert url.startswith(oauth_server.authorization_endpoint)
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"][0].endswith("/v1/connections/oauth/callback")
    assert query["client_id"][0] in oauth_server.clients
    state = query["state"][0]

    code = oauth_server.approve(url, code="c1")
    callback = await session_client.get(
        "/v1/connections/oauth/callback", params={"code": code, "state": state}
    )
    assert callback.status_code == 302, callback.text
    assert callback.headers["location"] == f"/settings/connections?connection={connection['id']}"

    assert await wait_for(dbos_client, workflow_id) == "SUCCESS"
    assert [call[0] for call in oauth_server.calls] == ["discover", "register", "exchange"]

    after = await session_client.get(f"/v1/connections/{connection['id']}")
    assert after.status_code == 200, after.text
    assert after.json()["status"] == "ok"
    tokens = await ConnectionTokenStorage(
        workspace.ctx, after.json()["id"], clock=clock
    ).get_tokens()
    assert tokens is not None
    assert tokens.refresh_token
    for pending in rows(db, "SELECT * FROM oauth_pending"):
        assert b"c1" not in text_of(pending)


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P3-02")
async def test_rotated_refresh_token_persisted_before_next_call(
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    master_key_file: MasterKeyFile,
    oauth_server: FakeOAuthServer,
    clock: FixedClock,
) -> None:
    """T-P3-02-04
    A token set that expires in one second has expired; two fetches ask for an access
    token at once. The fake token endpoint sees one refresh (the second caller waits on
    the connection row's lock, then finds the fresh tokens), both get the new access
    token, and the rotated refresh token is stored before either returns: the old one is
    dead at the server, and a later call uses the stored token without refreshing again.
    """
    from tumnis.modules.integrations.api import (  # noqa: PLC0415
        ConnectionTokenStorage,
        access_token,
        oauth_server_of,
    )

    connection_id = await connected(workspace.ctx, oauth_server, clock, expires_in=1)
    storage = ConnectionTokenStorage(workspace.ctx, connection_id, clock=clock)
    before = await storage.get_tokens()
    assert before is not None
    assert before.refresh_token
    clock.advance(timedelta(seconds=2))

    first, second = await asyncio.gather(
        access_token(workspace.ctx, connection_id, oauth=oauth_server, clock=clock),
        access_token(workspace.ctx, connection_id, oauth=oauth_server, clock=clock),
    )

    assert oauth_server.refreshes == 1
    after = await storage.get_tokens()
    assert after is not None
    assert first == second == after.access_token != before.access_token
    assert after.refresh_token
    assert after.refresh_token != before.refresh_token
    assert not oauth_server.refresh_token_live(before.refresh_token)

    server = await oauth_server_of(workspace.ctx, connection_id)
    assert server is not None
    again = await access_token(workspace.ctx, connection_id, oauth=oauth_server, clock=clock)
    assert again == after.access_token
    assert oauth_server.refreshes == 1
