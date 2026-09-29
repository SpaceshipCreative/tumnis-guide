"""Helpers for the audit log spec tests (P0-15). No assertions live here: spec-guard locks
the test bodies, and this module is where later work packages plug in their shapes.

- `configured(db)`: tumnis.core.db pointed at the per-test database (no pooling).
- `write_rows(ctx, n, clock)`: n audit rows through `audit.record`, one transaction each,
  the clock advanced a minute between them.
- `tamper(db, sql, params)`: an owner statement with the immutability trigger disabled,
  the way someone with the owner password could edit the table.
- `insert_raw(conn, workspace_id)`: one row as the owner, without the writer.
- `signed_in(app, workspace_id, user_id)`: an httpx client signed in as `user:<user_id>`
  in the workspace with a real session (P0-13): the user is made when missing, the session
  row is created through the auth module (no sign-in route runs, so nothing is audited),
  and the client sends the CSRF token and an Idempotency-Key on writes.
- `audit_ctx(app, db, clock)`: the `Ctx` the cases in backend/tests/audit_cases.py drive.
- `sign_in`, `create_key`, `change_secret_setting`: the real actions T-P0-15-10 checks;
  P0-13 (sign-in), P0-14 (keys) and P0-26 (the secret setting over HTTP) filled them.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import psycopg
from pydantic import BaseModel, ConfigDict

from tests._pg import OWNER

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from tests._pg import DbUrls
    from tumnis.core.clock import FixedClock
    from tumnis.core.tenancy import WorkspaceContext

# What ASGITransport reports as the peer address.
TEST_SOURCE_IP = "127.0.0.1"
TRIGGER = "audit_log_immutable"


@asynccontextmanager
async def configured(db: DbUrls) -> AsyncIterator[None]:
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    try:
        yield
    finally:
        await core_db.dispose()


async def write_rows(
    ctx: WorkspaceContext,
    n: int,
    clock: FixedClock,
    *,
    actions: tuple[str, ...] = ("test.recorded",),
) -> None:
    """n rows in ctx's workspace, one transaction each, cycling through `actions`."""
    from tumnis.core import audit  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    for i in range(n):
        async with tenant_session(ctx) as s:
            await audit.record(
                s, actions[i % len(actions)], details={"n": i + 1}, occurred_at=clock.now()
            )
        clock.advance(minutes=1)


def tamper(db: DbUrls, statement: str, params: tuple[Any, ...] = ()) -> int:
    """Run `statement` as the owner with the immutability trigger off; returns rowcount."""
    with psycopg.connect(db.libpq(OWNER)) as conn:
        conn.execute(f"ALTER TABLE audit_log DISABLE TRIGGER {TRIGGER}")
        rowcount = conn.execute(statement.encode(), params).rowcount
        conn.execute(f"ALTER TABLE audit_log ENABLE TRIGGER {TRIGGER}")
        return int(rowcount)


def insert_raw(conn: psycopg.Connection[Any], workspace_id: uuid.UUID, seq: int = 1) -> None:
    """One row written directly, as the owner (for the grant and trigger tests)."""
    conn.execute(
        "INSERT INTO audit_log (workspace_id, seq, occurred_at, actor_type, action, "
        "prev_hash, hash) VALUES (%s, %s, now(), 'system', 'test.raw', %s, %s)",
        (workspace_id, seq, bytes(32), bytes(32)),
    )


def owner_rows(db: DbUrls, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(query.encode(), params).fetchall()


def _owner_dsn(app: FastAPI) -> str:
    url: str = app.state.settings.database_owner_url
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def ensure_user(app: FastAPI, workspace_id: uuid.UUID, user_id: uuid.UUID) -> str:
    """The user `user_id` (an owner of the workspace, password TEST_PASSWORD, no TOTP yet),
    made as the owner role when missing; returns its email."""
    from tests.fixtures import _test_password_hash  # noqa: PLC0415

    email = f"user-{user_id.hex[-12:]}@example.test"
    with psycopg.connect(_owner_dsn(app), autocommit=True) as conn:
        made = conn.execute(
            "INSERT INTO users (id, email, password_hash, home_workspace_id)"
            " VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING RETURNING id",
            (user_id, email, _test_password_hash(), workspace_id),
        ).fetchone()
        if made is not None:
            conn.execute(
                "INSERT INTO memberships (workspace_id, user_id, role, created_by)"
                " VALUES (%s, %s, 'owner', 'system')",
                (workspace_id, user_id),
            )
    return email


@asynccontextmanager
async def signed_in(
    app: FastAPI, workspace_id: uuid.UUID, user_id: uuid.UUID, **headers: str
) -> AsyncIterator[httpx.AsyncClient]:
    import httpx  # noqa: PLC0415

    from tests._auth import (  # noqa: PLC0415
        BASE_URL,
        CSRF_COOKIE,
        SESSION_COOKIE,
        SessionClient,
        open_session,
    )

    ensure_user(app, workspace_id, user_id)
    token, csrf = await open_session(app, workspace_id, user_id)
    async with SessionClient(
        transport=httpx.ASGITransport(app=app),
        base_url=BASE_URL,
        headers=headers,
        cookies={SESSION_COOKIE: token, CSRF_COOKIE: csrf},
    ) as client:
        yield client


@dataclass(frozen=True)
class Ctx:
    """What an AuditCase's `perform` gets (backend/tests/audit_cases.py)."""

    app: FastAPI
    db: DbUrls
    clock: FixedClock
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    session_client: httpx.AsyncClient
    request_id: str
    source_ip: str = TEST_SOURCE_IP
    key_id: uuid.UUID | None = None  # P0-14: the key `key_client` signs with
    email: str | None = None  # P0-13: the user's sign-in credentials
    password: str | None = None
    totp_secret: str | None = None

    def actor_id(self, actor_type: str) -> uuid.UUID | None:
        return {"user": self.user_id, "api_key": self.key_id}.get(actor_type)


@asynccontextmanager
async def audit_ctx(app: FastAPI, db: DbUrls, clock: FixedClock) -> AsyncIterator[Ctx]:
    """A workspace, its user and a signed-in client that sends a fixed X-Request-ID."""
    from tests._auth import TEST_PASSWORD, enroll_totp  # noqa: PLC0415
    from tests.fixtures import make_workspace  # noqa: PLC0415

    workspace_id = make_workspace(db)
    user_id = uuid.uuid4()
    request_id = f"req-{uuid.uuid4().hex}"
    email = ensure_user(app, workspace_id, user_id)
    secret = await enroll_totp(user_id, workspace_id, clock.now())
    async with signed_in(app, workspace_id, user_id, **{"X-Request-ID": request_id}) as client:
        yield Ctx(
            app,
            db,
            clock,
            workspace_id,
            user_id,
            client,
            request_id,
            email=email,
            password=TEST_PASSWORD,
            totp_secret=secret,
        )


async def sign_in(ctx: Ctx) -> str:
    """Sign in with the password and the TOTP code at the clock's time through the routes
    (`auth.login`); returns the password used. P0-13. The client then holds only the new
    session's cookies (the ones the sign-in set, replacing the Ctx's own), so later writes
    through it send one CSRF token."""
    from tests._auth import Account  # noqa: PLC0415
    from tests._auth import sign_in as sign_in_routes  # noqa: PLC0415

    if not (ctx.email and ctx.password and ctx.totp_secret):
        raise RuntimeError("audit_ctx made this Ctx without sign-in credentials")
    account = Account(ctx.email, ctx.password, ctx.totp_secret, ctx.user_id, ctx.workspace_id)
    response = await sign_in_routes(ctx.session_client, account, ctx.clock)
    fresh = dict(response.cookies.items())
    ctx.session_client.cookies.clear()
    for name, value in fresh.items():
        ctx.session_client.cookies.set(name, value)
    return ctx.password


async def create_key(ctx: Ctx) -> str:
    """Create an API key through POST /v1/keys; returns the secret shown once. P0-14."""
    response = await ctx.session_client.post(
        "/v1/keys", json={"name": "audit secrets", "scopes": ["tasks:read"]}
    )
    response.raise_for_status()
    key: str = response.json()["key"]
    return key


# A section registered by these tests: phase 0 has no provider slot of its own yet (the
# first real one, `decisions.jev`, arrives with P1-01), so T-P0-15-10 writes through the
# real /v1/settings/{section} route into this one.
AUDIT_SECRET_SECTION = "test.audit_secret"


class AuditSecret(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str | None = None
    label: str = ""


def register_audit_secret_section() -> None:
    from tumnis.core.settings_store import SettingSection, register_section  # noqa: PLC0415

    register_section(
        SettingSection(AUDIT_SECRET_SECTION, AuditSecret, secret_fields=frozenset({"token"}))
    )


async def change_secret_setting(ctx: Ctx) -> str:
    """Change an encrypted workspace setting over HTTP (PUT /v1/settings/{section}, P0-26);
    returns its plaintext."""
    register_audit_secret_section()
    plaintext = f"sk-audit-{uuid.uuid4().hex}"
    response = await ctx.session_client.put(
        f"/v1/settings/{AUDIT_SECRET_SECTION}",
        json={"values": {"token": plaintext, "label": "audit"}, "version": None},
    )
    response.raise_for_status()
    return plaintext
