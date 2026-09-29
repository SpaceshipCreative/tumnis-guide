"""Helpers for the audit log spec tests (P0-15). No assertions live here: spec-guard locks
the test bodies, and this module is where later work packages plug in their shapes.

- `configured(db)`: tumnis.core.db pointed at the per-test database (no pooling).
- `write_rows(ctx, n, clock)`: n audit rows through `audit.record`, one transaction each,
  the clock advanced a minute between them.
- `tamper(db, sql, params)`: an owner statement with the immutability trigger disabled,
  the way someone with the owner password could edit the table.
- `insert_raw(conn, workspace_id)`: one row as the owner, without the writer.
- `signed_in(app, workspace_id, user_id)`: an httpx client whose requests count as a
  signed-in session of `user:<user_id>` in the workspace. Until P0-13's `session_client`
  exists it overrides the audit routes' session dependency; P0-13 swaps the body for a
  real login, and the tests keep calling it.
- `audit_ctx(app, db, clock)`: the `Ctx` the cases in backend/tests/audit_cases.py drive.
- `sign_in`, `create_key`, `change_secret_setting`: the real actions T-P0-15-10 checks;
  P0-13, P0-14 and P0-08 fill them in.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import psycopg
from fastapi import Request  # at runtime: FastAPI reads the override's annotations

from tests._pg import OWNER

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from tests._pg import DbUrls
    from tumnis.core.clock import FixedClock
    from tumnis.core.tenancy import WorkspaceContext

# What ASGITransport reports as the peer address.
TEST_SOURCE_IP = "127.0.0.1"
SESSION_HEADER = "X-Test-Session"
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


def _test_session(request: Request) -> WorkspaceContext:
    from fastapi import HTTPException  # noqa: PLC0415

    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415

    value = request.headers.get(SESSION_HEADER)
    if value is None:
        raise HTTPException(status_code=401, detail="unauthenticated")
    workspace_id, user_id = value.split(":")
    return WorkspaceContext(uuid.UUID(workspace_id), ActorRef(f"user:{user_id}"))


@asynccontextmanager
async def signed_in(
    app: FastAPI, workspace_id: uuid.UUID, user_id: uuid.UUID, **headers: str
) -> AsyncIterator[httpx.AsyncClient]:
    import httpx  # noqa: PLC0415

    from tumnis.core.audit_router import require_session  # noqa: PLC0415

    app.dependency_overrides[require_session] = _test_session
    transport = httpx.ASGITransport(app=app)
    default = {SESSION_HEADER: f"{workspace_id}:{user_id}", **headers}
    async with httpx.AsyncClient(
        transport=transport, base_url="https://test", headers=default
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

    def actor_id(self, actor_type: str) -> uuid.UUID | None:
        return {"user": self.user_id, "api_key": self.key_id}.get(actor_type)


@asynccontextmanager
async def audit_ctx(app: FastAPI, db: DbUrls, clock: FixedClock) -> AsyncIterator[Ctx]:
    """A workspace, its user and a signed-in client that sends a fixed X-Request-ID."""
    from tests.fixtures import make_workspace  # noqa: PLC0415

    workspace_id = make_workspace(db)
    user_id = uuid.uuid4()
    request_id = f"req-{uuid.uuid4().hex}"
    async with signed_in(app, workspace_id, user_id, **{"X-Request-ID": request_id}) as client:
        yield Ctx(app, db, clock, workspace_id, user_id, client, request_id)


async def sign_in(ctx: Ctx) -> str:
    """Sign in with the password (and TOTP); returns the password used. P0-13."""
    raise NotImplementedError("P0-13: login routes and session_client")


async def create_key(ctx: Ctx) -> str:
    """Create an API key through POST /v1/keys; returns the secret shown once. P0-14."""
    raise NotImplementedError("P0-14: API keys")


async def change_secret_setting(ctx: Ctx) -> str:
    """Change an encrypted workspace setting; returns its plaintext. P0-08 and P0-14."""
    raise NotImplementedError("P0-08: encrypted workspace settings over HTTP")
