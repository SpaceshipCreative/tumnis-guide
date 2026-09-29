"""API keys: shown once, stored as prefix and HMAC, expiry, rotation, last use, scopes and
events (P0-14, SEC-2, FR-9.3, FR-14.10)."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._keys import CANARY_PREFIX, bearer_client, canary_test_app
from tests._pg import OWNER

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import Fakes, KeyClientFactory, MasterKeyFile, PepperFile, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

OPEN = f"/v1{CANARY_PREFIX}/open"


@pytest.fixture
def app(  # noqa: PLR0917
    db: DbUrls,
    dbos_sys_db: DbUrls,
    clock: FixedClock,
    fakes: Fakes,
    master_key_file: MasterKeyFile,
    pepper_file: PepperFile,
) -> FastAPI:
    """The shared app with the canary routes, so a key has a route to call."""
    return canary_test_app(db, dbos_sys_db, clock, str(pepper_file.path))


def _owner_row(db: DbUrls, query: str, params: tuple[Any, ...]) -> Any:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(query.encode(), params).fetchone()


def _parts(key: str) -> tuple[str, str]:
    _kind, prefix, secret = key.split("_", 2)
    return prefix, secret


@pytest.mark.req("SEC-2", "FR-9.3")
@pytest.mark.wp("P0-14")
@pytest.mark.xfail(strict=True, reason="spec:P0-14")
async def test_key_is_shown_once_and_stored_as_prefix_and_hmac(
    app: FastAPI, session_client: SessionClient, db: DbUrls, pepper_file: PepperFile
) -> None:
    """T-P0-14-02
    Creating a key answers 201 with the key once; `GET /v1/keys` lists it without the
    secret; the raw row holds the prefix and HMAC(pepper, secret) and no column contains
    the secret; an idempotent replay of the create answers the same key record without
    `key`. The key authenticates as an API key principal.
    """
    body = {"name": "laptop script", "scopes": ["tasks:read"]}
    headers = {"Idempotency-Key": "create-once"}
    created = await session_client.post("/v1/keys", json=body, headers=headers)
    assert created.status_code == 201, created.text
    out = created.json()
    key = out["key"]
    assert key.startswith("tmn_")
    prefix, secret = _parts(key)
    assert out["prefix"] == prefix
    assert out["name"] == "laptop script"
    assert out["scopes"] == ["tasks:read"]

    listed = await session_client.get("/v1/keys")
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert [item["id"] for item in items] == [out["id"]]
    assert "key" not in items[0]
    assert items[0]["prefix"] == prefix
    assert secret not in listed.text

    row = _owner_row(
        db,
        "SELECT prefix, secret_hmac, pepper_version, to_jsonb(k)::text FROM api_keys k"
        " WHERE id = %s",
        (uuid.UUID(out["id"]),),
    )
    assert row is not None
    stored_prefix, secret_hmac, pepper_version, everything = row
    assert stored_prefix == prefix
    assert pepper_version == pepper_file.active
    pepper = pepper_file.keys[pepper_file.active]
    assert bytes(secret_hmac) == hmac.new(pepper, secret.encode(), hashlib.sha256).digest()
    assert secret not in everything
    assert key not in everything

    replay = await session_client.post("/v1/keys", json=body, headers=headers)
    assert replay.status_code == 201
    assert replay.headers.get("Idempotent-Replayed") == "true"
    assert replay.json()["id"] == out["id"]
    assert "key" not in replay.json()
    assert secret not in replay.text

    async with bearer_client(app, key) as client:
        who = await client.get(OPEN)
    assert who.status_code == 200, who.text
    assert who.json()["principal"] == "api_key"


@pytest.mark.req("SEC-2")
@pytest.mark.wp("P0-14")
@pytest.mark.xfail(strict=True, reason="spec:P0-14")
async def test_expired_key_is_refused(
    app: FastAPI, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P0-14-05
    A key with `expires_at` works until then; with the clock past it, the answer is 401
    `unauthenticated` with detail `key_expired`.
    """
    from tumnis.modules.auth import api  # noqa: PLC0415

    body = api.KeyIn(
        name="short", scopes=["tasks:read"], expires_at=clock.now() + timedelta(hours=1)
    )
    created = await api.create_key(workspace.ctx, body, now=clock.now())
    async with bearer_client(app, created.key) as client:
        before = await client.get(OPEN)
        clock.advance(hours=2)
        after = await client.get(OPEN)
    assert before.status_code == 200, before.text
    assert after.status_code == 401, after.text
    problem = after.json()
    assert problem["code"] == "unauthenticated"
    assert problem["detail"] == "key_expired"


@pytest.mark.req("SEC-2")
@pytest.mark.wp("P0-14")
@pytest.mark.xfail(strict=True, reason="spec:P0-14")
async def test_rotation_replaces_the_secret(
    app: FastAPI, session_client: SessionClient, clock: FixedClock
) -> None:
    """T-P0-14-06
    Rotating gives a new secret (and prefix) on the same key record: the new key works and
    the old one fails at once; rotating with `grace_minutes` keeps the previous secret
    working until the window ends. The rotate response is shown once (a replay has no
    `key`); a grace window over 1,440 minutes is 422.
    """
    created = await session_client.post("/v1/keys", json={"name": "ci", "scopes": ["tasks:read"]})
    assert created.status_code == 201, created.text
    key_id, first = created.json()["id"], created.json()["key"]

    rotated = await session_client.post(
        f"/v1/keys/{key_id}/rotate", json={}, headers={"Idempotency-Key": "rotate-1"}
    )
    assert rotated.status_code == 200, rotated.text
    second = rotated.json()["key"]
    assert rotated.json()["id"] == key_id
    assert second != first
    assert _parts(second)[0] != _parts(first)[0]
    replay = await session_client.post(
        f"/v1/keys/{key_id}/rotate", json={}, headers={"Idempotency-Key": "rotate-1"}
    )
    assert replay.headers.get("Idempotent-Replayed") == "true"
    assert "key" not in replay.json()

    async with bearer_client(app, first) as old, bearer_client(app, second) as new:
        assert (await new.get(OPEN)).status_code == 200
        assert (await old.get(OPEN)).status_code == 401

    graced = await session_client.post(f"/v1/keys/{key_id}/rotate", json={"grace_minutes": 10})
    assert graced.status_code == 200, graced.text
    third = graced.json()["key"]
    async with bearer_client(app, second) as old, bearer_client(app, third) as new:
        assert (await old.get(OPEN)).status_code == 200
        assert (await new.get(OPEN)).status_code == 200
        clock.advance(minutes=11)
        assert (await old.get(OPEN)).status_code == 401
        assert (await new.get(OPEN)).status_code == 200

    too_long = await session_client.post(f"/v1/keys/{key_id}/rotate", json={"grace_minutes": 1_441})
    assert too_long.status_code == 422


@pytest.mark.req("SEC-2")
@pytest.mark.wp("P0-14")
@pytest.mark.xfail(strict=True, reason="spec:P0-14")
async def test_last_use_is_tracked_at_most_once_a_minute(
    key_client: KeyClientFactory, db: DbUrls, clock: FixedClock
) -> None:
    """T-P0-14-07
    Two calls 10 s apart write `last_used_at` once (the first call's time); a call 61 s
    after the first updates it.
    """
    client = await key_client(frozenset({"tasks:read"}))
    key_id = client.key_id  # type: ignore[attr-defined]
    query = "SELECT last_used_at, version FROM api_keys WHERE id = %s"
    start = clock.now()
    assert _owner_row(db, query, (key_id,))[0] is None

    assert (await client.get(OPEN)).status_code == 200
    first_seen, version = _owner_row(db, query, (key_id,))
    assert first_seen == start
    clock.advance(seconds=10)
    assert (await client.get(OPEN)).status_code == 200
    assert _owner_row(db, query, (key_id,)) == (start, version)

    clock.advance(seconds=51)
    assert (await client.get(OPEN)).status_code == 200
    assert _owner_row(db, query, (key_id,))[0] == start + timedelta(seconds=61)
    await client.aclose()


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P0-14")
@pytest.mark.xfail(strict=True, reason="spec:P0-14")
async def test_unknown_scope_is_rejected(session_client: SessionClient, db: DbUrls) -> None:
    """T-P0-14-08
    `scopes: ["tasks:admin"]` is 422 `unknown_scope` and stores nothing.
    """
    response = await session_client.post(
        "/v1/keys", json={"name": "admin", "scopes": ["tasks:read", "tasks:admin"]}
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "unknown_scope"
    assert _owner_row(db, "SELECT count(*) FROM api_keys", ()) == (0,)


@pytest.mark.req("SEC-2")
@pytest.mark.wp("P0-14")
@pytest.mark.xfail(strict=True, reason="spec:P0-14")
async def test_key_events_are_emitted(
    session_client: SessionClient, db: DbUrls, workspace: WorkspaceHandle
) -> None:
    """T-P0-14-16
    Creating a key emits `key.created` and revoking it (`DELETE /v1/keys/{id}`, 204)
    emits `key.revoked`, in the key's workspace, with the key id and no secret in either
    payload; the revoked key is listed with `revoked_at`.
    """
    created = await session_client.post("/v1/keys", json={"name": "bot", "scopes": ["ingest"]})
    assert created.status_code == 201, created.text
    key_id, key = created.json()["id"], created.json()["key"]
    _prefix, secret = _parts(key)
    revoked = await session_client.delete(f"/v1/keys/{key_id}")
    assert revoked.status_code == 204, revoked.text

    with psycopg.connect(db.libpq(OWNER)) as conn:
        rows = conn.execute(
            "SELECT name, workspace_id, payload FROM outbox WHERE name LIKE 'key.%%' ORDER BY id"
        ).fetchall()
    assert [name for name, _, _ in rows] == ["key.created", "key.revoked"]
    for _name, workspace_id, payload in rows:
        assert workspace_id == workspace.id
        assert payload["key_id"] == key_id
        text = json.dumps(payload)
        assert secret not in text
        assert key not in text

    listed = (await session_client.get("/v1/keys")).json()["items"]
    assert listed[0]["revoked_at"] is not None
