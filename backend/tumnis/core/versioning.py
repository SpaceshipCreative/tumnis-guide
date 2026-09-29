"""Optimistic concurrency: a write names the version it read (REL-2).

P0-08 lands `StaleVersion` and `update_versioned` for the settings writers; P0-10 adds the
409 problem handler, `soft_delete_versioned` and the rest of the write conventions here.
"""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, Table, select, update
from sqlalchemy.ext.asyncio import AsyncSession


class StaleVersion(Exception):  # noqa: N818  # the plan's name (A13, P0-10)
    """The row moved on since the caller read it; `current` is what it holds now."""

    def __init__(self, current: Mapping[str, Any]) -> None:
        super().__init__("stale version")
        self.current = current


class NotFound(LookupError):  # noqa: N818  # the plan's name (P0-10)
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
