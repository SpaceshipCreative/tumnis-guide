"""`minimal_row(conn, table, workspace_id)`: a valid row for any table, built from the
catalog, for tables no seed covers (P0-06). Spec stub."""

from typing import Any
from uuid import UUID

import psycopg


def minimal_row(conn: psycopg.Connection[Any], table: str, workspace_id: UUID) -> dict[str, Any]:
    raise NotImplementedError("P0-06")


def fill_every_table(conn: psycopg.Connection[Any]) -> list[str]:
    raise NotImplementedError("P0-06")
