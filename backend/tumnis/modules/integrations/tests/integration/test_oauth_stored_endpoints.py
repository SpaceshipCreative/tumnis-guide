"""The OAuth endpoints a connection keeps are checked again when they are used (P3-02
follow-up; SEC-9; MCP authorization spec, Communication Security): a stored sign-in page
that is not https starts no consent, registers nothing and leaves no pending row. The
token endpoint is checked by the client on every request (`test_oauth_https.py`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tumnis.modules.integrations.tests.integration._connections import rows

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import MasterKeyFile, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("SEC-9", "FR-14.4")
@pytest.mark.wp("P3-02")
async def test_a_stored_plain_http_sign_in_page_is_refused(
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    master_key_file: MasterKeyFile,
    clock: FixedClock,
) -> None:
    from tumnis.core.adapters.errors import AdapterRejected  # noqa: PLC0415
    from tumnis.modules.integrations import api  # noqa: PLC0415
    from tumnis.modules.integrations.adapters.fake_oauth import FakeOAuthServer  # noqa: PLC0415
    from tumnis.modules.integrations.oauth_port import OAuthServer  # noqa: PLC0415

    made = await api.create_connection(
        workspace.ctx, "fake", api.ConnectionSettings(), account_label="Work"
    )
    issuer = "https://auth.fake.example"
    await api.store_oauth_server(
        workspace.ctx,
        made.id,
        OAuthServer(
            resource="https://mcp.fake.example/mcp",
            issuer=issuer,
            authorization_endpoint="http://auth.fake.example/authorize",
            token_endpoint=f"{issuer}/token",
            registration_endpoint=f"{issuer}/register",
        ),
    )
    oauth = FakeOAuthServer()

    with pytest.raises(AdapterRejected):
        await api.prepare_oauth(
            workspace.ctx,
            made.id,
            oauth=oauth,
            redirect_uri="https://tumnis.example.org/v1/connections/oauth/callback",
            workflow_id="connect-test",
            now=clock.now(),
        )

    assert oauth.calls == []
    assert rows(app_db, "SELECT id FROM oauth_pending WHERE connection_id = %s", made.id) == []
