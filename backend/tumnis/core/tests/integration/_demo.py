"""Demo router and `demo_items` table for the P0-10 spec tests. No assertions live here:
spec-guard locks the test bodies, and this module is the harness they drive.

- `demo_items`: a tenant table (base columns plus `title text`, `due_on date`) created at
  fixture time with P0-06's `create_tenant_table`, run through Alembic's operations on the
  per-test database.
- `build_router(state)`: `POST /v1/demo-items` (idempotent), `PATCH /v1/demo-items/{item_id}`
  (idempotent, versioned) and `GET /v1/demo-items` (paginated, `sort=id|due_on`), declared
  through `v1_router` and `route_policy`. `DemoState` counts handler runs and scripts a
  delay or a failure inside the write's transaction.
- `TestPrincipalMiddleware`: `X-Test-Principal: <workspace>:<principal>` becomes a session
  principal on `request.state.principal`. It stands in for P0-13's authentication
  middleware and is installed only by `demo_app`.
- Fixtures: `demo_items` (creates the table), `demo_app` (`create_app(extra_routers=[demo])` plus
  the test principal middleware; yields `Demo(app, state, clock)`), `demo_client` (httpx
  on it; application errors come back as 500 responses instead of raising).
- Helpers: `principal_header(workspace, principal)`, `client_at(app, address)` (a client
  whose requests come from that source address), `insert_items(db, workspace, dues)` (rows
  written as the owner), `count_items(db)`.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal

import psycopg
import pytest
import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from tests._pg import OWNER

if TYPE_CHECKING:
    import httpx
    from fastapi import APIRouter, FastAPI
    from starlette.types import ASGIApp, Receive, Scope, Send

    from tests._pg import DbUrls
    from tests.fixtures import Fakes, MasterKeyFile
    from tumnis.core.clock import FixedClock

PRINCIPAL_HEADER = "X-Test-Principal"
KEY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"
PROBLEM_JSON = "application/problem+json"

metadata = sa.MetaData()
demo_items = sa.Table(
    "demo_items",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("uuidv7()")),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("created_by", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("due_on", sa.Date),
)


@dataclass
class DemoState:
    posts: int = 0  # POST handler runs (inside the write's transaction)
    patches: int = 0
    fail_next: int = 0  # the POST handler raises this many more times
    delay_s: float = 0.0  # the POST handler sleeps this long inside its transaction


@dataclass(frozen=True)
class Demo:
    app: FastAPI
    state: DemoState
    clock: FixedClock


class DemoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    due_on: date | None = None


class DemoPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    due_on: date | None = None
    version: int


class DemoItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    due_on: date | None
    version: int
    created_at: datetime


def principal_header(workspace: Any, principal: uuid.UUID) -> dict[str, str]:
    """The test resolver's header: acts as `user:<principal>` in the workspace."""
    return {PRINCIPAL_HEADER: f"{getattr(workspace, 'id', workspace)}:{principal}"}


def build_router(state: DemoState) -> APIRouter:
    from fastapi import Depends, Query  # noqa: PLC0415

    from tumnis.core.idempotency import SessionDep  # noqa: PLC0415
    from tumnis.core.pagination import (  # noqa: PLC0415
        Page,
        PageParams,
        SortKey,
        page_params,
        paginate,
    )
    from tumnis.core.routing import RoutePolicy, route_policy, v1_router  # noqa: PLC0415
    from tumnis.core.versioning import update_versioned  # noqa: PLC0415

    router = v1_router("demo", prefix="/demo-items", tags=["demo"])
    t = demo_items

    @router.post("", status_code=201)
    @route_policy(RoutePolicy(auth="session_or_key", idempotent=True))
    async def create_demo_item(body: DemoIn, session: SessionDep) -> DemoItem:
        state.posts += 1
        row = (
            (
                await session.execute(
                    sa.insert(t).values(title=body.title, due_on=body.due_on).returning(*t.c)
                )
            )
            .mappings()
            .one()
        )
        if state.delay_s:
            await asyncio.sleep(state.delay_s)
        if state.fail_next > 0:
            state.fail_next -= 1
            raise RuntimeError("demo failure")
        return DemoItem.model_validate(row)

    @router.patch("/{item_id}")
    @route_policy(RoutePolicy(auth="session_or_key", idempotent=True))
    async def update_demo_item(
        item_id: uuid.UUID, body: DemoPatch, session: SessionDep
    ) -> DemoItem:
        state.patches += 1
        values = body.model_dump(exclude={"version"}, exclude_unset=True)
        row = await update_versioned(session, t, item_id, body.version, values)
        return DemoItem.model_validate(row)

    @router.get("")
    @route_policy(RoutePolicy(auth="session_or_key", paginated=True))
    async def list_demo_items(
        session: SessionDep,
        page: Annotated[PageParams, Depends(page_params)],
        sort: Annotated[Literal["id", "due_on"], Query()] = "id",
    ) -> Page[DemoItem]:
        keys = [SortKey(t.c.due_on, nulls_last_sentinel=date.max)] if sort == "due_on" else []
        stmt = sa.select(t).where(t.c.deleted_at.is_(None))
        return await paginate(
            session,
            stmt,
            keys=keys,
            id_col=t.c.id,
            cursor=page.cursor,
            limit=page.limit,
            model=DemoItem,
        )

    return router


class TestPrincipalMiddleware:
    """`X-Test-Principal: <workspace>:<principal>` -> a session principal (until P0-13)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            from tumnis.core.principal import Principal  # noqa: PLC0415

            headers = dict(scope.get("headers", []))
            value = headers.get(PRINCIPAL_HEADER.lower().encode())
            if value is not None:
                workspace, principal = value.decode().split(":")
                scope.setdefault("state", {})["principal"] = Principal(
                    kind="session",
                    workspace_id=uuid.UUID(workspace),
                    subject_id=uuid.UUID(principal),
                )
        await self.app(scope, receive, send)


def create_demo_table(db: DbUrls) -> None:
    """demo_items through P0-06's helper, as the owner, on the per-test database."""
    from alembic.operations import Operations  # noqa: PLC0415
    from alembic.runtime.migration import MigrationContext  # noqa: PLC0415

    from tumnis.core.migration_helpers import create_tenant_table  # noqa: PLC0415

    engine = sa.create_engine(db.owner)
    try:
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            create_tenant_table(
                "demo_items",
                sa.Column("title", sa.Text, nullable=False),
                sa.Column("due_on", sa.Date, nullable=True),
            )
    finally:
        engine.dispose()


def insert_items(db: DbUrls, workspace: Any, dues: Sequence[date | None]) -> list[uuid.UUID]:
    """One row per due date in the workspace, as the owner; returns their ids in order."""
    workspace_id = getattr(workspace, "id", workspace)
    ids = []
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        for i, due in enumerate(dues):
            row = conn.execute(
                "INSERT INTO demo_items (workspace_id, title, due_on) VALUES (%s, %s, %s)"
                " RETURNING id",
                (workspace_id, f"row {i}", due),
            ).fetchone()
            assert row is not None
            ids.append(row[0])
    return ids


def count_items(db: DbUrls) -> int:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        row = conn.execute("SELECT count(*) FROM demo_items").fetchone()
    return int(row[0]) if row else 0


@pytest.fixture(name="demo_items")
def demo_items_fixture(db: DbUrls) -> sa.Table:
    create_demo_table(db)
    return demo_items


@pytest.fixture
async def demo_app(  # noqa: PLR0917
    db: DbUrls,
    dbos_sys_db: DbUrls,
    clock: FixedClock,
    fakes: Fakes,
    master_key_file: MasterKeyFile,
    demo_items: sa.Table,
) -> AsyncIterator[Demo]:
    from tests.fixtures import settings_for  # noqa: PLC0415
    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415

    state = DemoState()
    app = create_app(
        settings=settings_for(db, dbos_sys_db), clock=clock, extra_routers=[build_router(state)]
    )
    app.add_middleware(TestPrincipalMiddleware)
    try:
        yield Demo(app, state, clock)
    finally:
        await core_db.dispose()


@asynccontextmanager
async def client_at(app: FastAPI, address: str = "127.0.0.1") -> AsyncIterator[httpx.AsyncClient]:
    """An httpx client whose requests come from `address`; a raised application error comes
    back as its 500 response."""
    import httpx  # noqa: PLC0415

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=(address, 50000))
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        yield client


@pytest.fixture
async def demo_client(demo_app: Demo) -> AsyncIterator[httpx.AsyncClient]:
    async with client_at(demo_app.app) as client:
        yield client
