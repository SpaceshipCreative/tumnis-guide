"""Connections (P3-02): grants sealed per account, an API that never hands credentials
back, and audited connect, disconnect and OAuth state mismatches (Data flow rules 1 and 5,
SEC-3)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.integrations.tests.integration._connections import (
    connect_through_routes,
    connected,
    rows,
    text_of,
)

if TYPE_CHECKING:
    from dbos import DBOSClient
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import MasterKeyFile, WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.integrations.adapters.fake_oauth import FakeOAuthServer

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

# Names a credential could hide under in an answer or a schema.
CREDENTIAL_KEYS = frozenset(
    {
        "credentials",
        "credentials_enc",
        "access_token",
        "refresh_token",
        "tokens",
        "token",
        "client_info",
        "client_secret",
        "code",
        "code_verifier",
    }
)


def _keys(value: Any) -> set[str]:
    """Every object key anywhere in a JSON value."""
    if isinstance(value, dict):
        found = set(value)
        for item in value.values():
            found |= _keys(item)
        return found
    if isinstance(value, list):
        found = set()
        for item in value:
            found |= _keys(item)
        return found
    return set()


@pytest.mark.req("Data flow rule 1", "Data flow rule 5")
@pytest.mark.wp("P3-02")
@pytest.mark.xfail(strict=True, reason="spec:P3-02")
async def test_grant_stored_encrypted_per_account(
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    master_key_file: MasterKeyFile,
    oauth_server: FakeOAuthServer,
    clock: FixedClock,
) -> None:
    """T-P3-02-01
    Two accounts of one provider are two connections, each with its own sealed grant: two
    different ciphertexts, and no column of either row holds an access or refresh token in
    plaintext. Each opens again to its own tokens through `ConnectionTokenStorage`.
    """
    from tumnis.modules.integrations.api import ConnectionTokenStorage  # noqa: PLC0415

    work = await connected(workspace.ctx, oauth_server, clock, label="Work")
    home = await connected(workspace.ctx, oauth_server, clock, label="Personal")

    stored = {
        row["id"]: row
        for row in rows(app_db, "SELECT * FROM connections WHERE id = ANY(%s)", [work, home])
    }
    assert set(stored) == {work, home}
    assert stored[work]["provider"] == stored[home]["provider"] == "fake"
    assert stored[work]["credentials_enc"]
    assert stored[home]["credentials_enc"]
    assert bytes(stored[work]["credentials_enc"]) != bytes(stored[home]["credentials_enc"])

    opened = {
        conn: await ConnectionTokenStorage(workspace.ctx, conn, clock=clock).get_tokens()
        for conn in (work, home)
    }
    secrets = []
    for tokens in opened.values():
        assert tokens is not None
        assert tokens.refresh_token
        secrets += [tokens.access_token, tokens.refresh_token]
    assert len(set(secrets)) == 4  # each account has its own pair
    for row in stored.values():
        for secret in secrets:
            assert secret.encode() not in text_of(row)


@pytest.mark.req("Data flow rule 5")
@pytest.mark.wp("P3-02")
@pytest.mark.xfail(strict=True, reason="spec:P3-02")
async def test_connection_api_never_returns_credentials(  # noqa: PLR0917
    app: FastAPI,
    session_client: SessionClient,
    workspace: WorkspaceHandle,
    oauth_server: FakeOAuthServer,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P3-02-02
    A connected account read through `GET /v1/connections/{id}`, listed through
    `GET /v1/connections` and renamed through `PATCH` never carries a credential field or
    a token's text, and the OpenAPI schemas of the connection models declare none.
    """
    from tumnis.modules.integrations.api import ConnectionTokenStorage  # noqa: PLC0415

    connection_id = await connected(workspace.ctx, oauth_server, clock, label="Work")
    tokens = await ConnectionTokenStorage(workspace.ctx, connection_id, clock=clock).get_tokens()
    assert tokens is not None
    assert tokens.refresh_token
    secrets = (tokens.access_token, tokens.refresh_token)

    one = await session_client.get(f"/v1/connections/{connection_id}")
    assert one.status_code == 200, one.text
    listed = await session_client.get("/v1/connections")
    assert listed.status_code == 200, listed.text
    patched = await session_client.patch(
        f"/v1/connections/{connection_id}",
        json={
            "account_label": "Work mail",
            "settings": {"backfill_days": 60},
            "version": one.json()["version"],
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["account_label"] == "Work mail"
    assert patched.json()["settings"]["backfill_days"] == 60

    for answer in (one, listed, patched):
        assert not _keys(answer.json()) & CREDENTIAL_KEYS, answer.json()
        for secret in secrets:
            assert secret not in answer.text
    assert str(connection_id) in {item["id"] for item in listed.json()}

    schemas = app.openapi()["components"]["schemas"]
    for name in ("ConnectionOut", "ConnectionSettings", "ConnectionCreate", "ConnectionPatch"):
        assert name in schemas, name
        assert not set(schemas[name].get("properties", {})) & CREDENTIAL_KEYS, name


@pytest.mark.req("SEC-3")
@pytest.mark.wp("P3-02")
@pytest.mark.xfail(strict=True, reason="spec:P3-02")
async def test_connect_and_disconnect_are_audited(  # noqa: PLR0917
    session_client: SessionClient,
    dbos_client: DBOSClient,
    workspace: WorkspaceHandle,
    oauth_server: FakeOAuthServer,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P3-02-12
    Connecting through OAuth writes `connector.connected` with the signed-in user as the
    actor; a callback whose state matches no sign-in answers 400 `oauth_state_mismatch`
    and writes `connector.oauth_state_mismatch`; `DELETE /v1/connections/{id}` with a
    reason writes `connector.disconnected` with that reason. A disconnected connection is
    gone from the API and holds no credentials, while its synced records stay (purging
    them is P3-09's).
    """
    connection_id, callback = await connect_through_routes(
        session_client, dbos_client, oauth_server
    )
    assert callback.status_code == 302, callback.text

    stray = await session_client.get(
        "/v1/connections/oauth/callback", params={"code": "c9", "state": "no-such-state"}
    )
    assert stray.status_code == 400, stray.text
    assert stray.json()["code"] == "oauth_state_mismatch"

    gone = await session_client.request(
        "DELETE", f"/v1/connections/{connection_id}", json={"reason": "Switching mailbox"}
    )
    assert gone.status_code == 204, gone.text
    assert (await session_client.get(f"/v1/connections/{connection_id}")).status_code == 404

    audit = {
        row["action"]: row
        for row in rows(
            db,
            "SELECT action, actor_type, actor_id, target_type, target_id, reason, details "
            "FROM audit_log WHERE action LIKE 'connector.%%' ORDER BY seq",
        )
    }
    assert set(audit) == {
        "connector.connected",
        "connector.oauth_state_mismatch",
        "connector.disconnected",
    }
    for action in ("connector.connected", "connector.disconnected"):
        assert audit[action]["actor_type"] == "user", action
        assert audit[action]["actor_id"] == workspace.user_id, action
        assert str(audit[action]["target_id"]) == connection_id, action
        assert audit[action]["details"]["provider"] == "fake", action
    assert audit["connector.oauth_state_mismatch"]["actor_type"] == "user"
    assert audit["connector.disconnected"]["reason"] == "Switching mailbox"

    (row,) = rows(
        db, "SELECT credentials_enc, status FROM connections WHERE id = %s", connection_id
    )
    assert row["credentials_enc"] is None
    assert row["status"] == "disabled"
