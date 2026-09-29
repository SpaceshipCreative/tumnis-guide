"""Optimistic concurrency: a write names the version it read (REL-2).

`update_versioned` and `soft_delete_versioned` match `version = expected` on a live row. A
stale version raises `StaleVersion` carrying the current row, which the problem handlers
(tumnis.core.errors) answer as 409 `stale_version` with `current`; a missing or deleted row
raises `NotFound` (404 `not_found`). Writers that shape their resource differently raise
`StaleVersion(current=<resource>)` themselves.

A request body names the version as `Version`: a Postgres `integer`, so a value past its
range is 422 `validation_error` rather than a database error (found by P0-11's fuzzer).
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Final
from uuid import UUID

from pydantic import Field
from sqlalchemy import RowMapping, Table, select, update
from sqlalchemy.ext.asyncio import AsyncSession

INT4_MAX: Final = 2_147_483_647
Version = Annotated[int, Field(ge=0, le=INT4_MAX)]


class StaleVersion(Exception):  # noqa: N818  # the plan's name (A13, P0-10)
    """The row moved on since the caller read it; `current` is what it holds now."""

    status = 409
    code = "stale_version"

    def __init__(self, current: Mapping[str, Any]) -> None:
        super().__init__("stale version")
        self.current = current


class NotFound(LookupError):  # noqa: N818  # the plan's name (P0-10)
    status = 404
    code = "not_found"

    def __init__(self, table: str, row_id: UUID) -> None:
        super().__init__(f"{table} {row_id} not found")
        self.table = table
        self.row_id = row_id


async def update_versioned(
    session: AsyncSession,
    table: Table,
    row_id: UUID,
    expected_version: int,
    values: Mapping[str, Any],
) -> RowMapping:
    """UPDATE ... WHERE id = row_id AND version = expected_version, returning the new row.
    A tenant table's touch trigger bumps `version` and `updated_at`; a table without it
    passes them in `values`. Stale raises StaleVersion with the current row; a missing or
    soft-deleted row raises NotFound."""
    stmt = (
        update(table)
        .where(
            table.c.id == row_id,
            table.c.version == expected_version,
            table.c.deleted_at.is_(None),
        )
        .values(**values)
        .returning(*table.c)
    )
    row = (await session.execute(stmt)).mappings().first()
    if row is not None:
        return row
    current = (await session.execute(select(table).where(table.c.id == row_id))).mappings().first()
    if current is None or current["deleted_at"] is not None:
        raise NotFound(table.name, row_id)
    raise StaleVersion(current=dict(current))


async def soft_delete_versioned(
    session: AsyncSession,
    table: Table,
    row_id: UUID,
    expected_version: int,
    *,
    deleted_at: datetime,
) -> RowMapping:
    """Set `deleted_at` at `expected_version` (the touch trigger bumps the version); the
    same StaleVersion and NotFound rules as update_versioned."""
    return await update_versioned(
        session, table, row_id, expected_version, {"deleted_at": deleted_at}
    )
