"""Helpers for the integrations spec tests (P0-12). No assertions live here: spec-guard
locks the test bodies, and this module is where later work packages plug in their shapes.

- `configured(db)`: tumnis.core.db pointed at the per-test database (no pooling).
- `new_connection(db, workspace_id, ...)`: a `connections` row, written as the owner
  (P3-02 brings `create_connection`; until then the tests insert the row themselves).
- `rows(db, table, connection_id)`: every row of a table for one connection, read as the
  owner (row-level security does not apply), ordered by external id.
- `item(...)` and `page(...)`: raw items and sync pages for the scripted connector.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from tests._pg import OWNER

if TYPE_CHECKING:
    from collections.abc import Iterable

    from tests._pg import DbUrls
    from tumnis.modules.integrations.api import RawItem, SyncPage

T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
LATER = T0 + timedelta(hours=1)


@asynccontextmanager
async def configured(db: DbUrls) -> AsyncIterator[None]:
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    try:
        yield
    finally:
        await core_db.dispose()


def new_connection(
    db: DbUrls,
    workspace_id: uuid.UUID,
    *,
    provider: str = "scripted",
    kind: str = "email",
    account: str | None = None,
) -> uuid.UUID:
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO connections (workspace_id, kind, provider, account, status) "
            "VALUES (%s, %s, %s, %s, 'ok') RETURNING id",
            (workspace_id, kind, provider, account or f"acct-{uuid.uuid4().hex[:12]}"),
        ).fetchone()
    if row is None:
        raise RuntimeError("no connection row returned")
    connection_id: uuid.UUID = row[0]
    return connection_id


def rows(db: DbUrls, table: str, connection_id: uuid.UUID) -> list[dict[str, Any]]:
    query = sql.SQL("SELECT * FROM {} WHERE connection_id = %s ORDER BY external_id").format(
        sql.Identifier(table)
    )
    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        return conn.execute(query, (connection_id,)).fetchall()


def by_id(db: DbUrls, table: str, row_id: uuid.UUID) -> dict[str, Any] | None:
    query = sql.SQL("SELECT * FROM {} WHERE id = %s").format(sql.Identifier(table))
    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        return conn.execute(query, (row_id,)).fetchone()


def scalar(db: DbUrls, query: str, *params: Any) -> Any:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        row = conn.execute(query, params).fetchone()
    return None if row is None else row[0]


def item(
    external_id: str, record_type: str = "message", fetched_at: datetime = T0, **payload: Any
) -> RawItem:
    from tumnis.modules.integrations.api import RawItem  # noqa: PLC0415

    return RawItem(
        external_id=external_id,
        record_type=record_type,
        fetched_at=fetched_at,
        payload={"id": external_id, **payload},
    )


def page(*items: RawItem, deleted: Iterable[str] = (), has_more: bool = False) -> SyncPage:
    from tumnis.modules.integrations.api import SyncPage  # noqa: PLC0415

    return SyncPage(items=list(items), deleted=list(deleted), next_cursor=None, has_more=has_more)
