"""Canonical keys: every table that holds provider records by external id carries the
common canonical columns and the unique key (workspace_id, connection_id, external_id)
(P0-12, FR-14.1, FR-14.3). Catalog-driven: a canonical table a later migration adds is
checked here without anyone listing it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from tests._pg import DbUrls

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

# The canonical tables P0-12 creates; the sweep covers any other table with external_id.
CANONICAL = {"people", "threads", "messages", "notes", "artifacts", "events", "documents"}
# Tables with an external_id that are not canonical records, each with its reason.
NOT_CANONICAL = {
    "raw_payloads": "the provider JSON behind the records, keyed by record type as well",
}
# Documents also hold text entries and uploads, which have no connection or external id:
# there the key columns are nullable and the unique key is partial (external_id IS NOT NULL).
OPTIONAL_KEY = {"documents"}
COMMON = {
    "connection_id": "uuid",
    "external_id": "text",
    "provider_url": "text",
    "fetched_at": "timestamp with time zone",
    "raw_payload_id": "uuid",
    "content_hash": "bytea",
    "tainted": "boolean",
    "source": "text",
}
KEY = ["workspace_id", "connection_id", "external_id"]
ALWAYS_NOT_NULL = {"content_hash", "tainted", "source"}
KEY_NOT_NULL = {"connection_id", "external_id", "fetched_at"}
FOREIGN = {"connection_id": "connections", "raw_payload_id": "raw_payloads"}

_TABLES = """
SELECT DISTINCT table_name FROM information_schema.columns
WHERE table_schema = 'public' AND column_name = 'external_id'
"""
_COLUMNS = """
SELECT column_name, data_type, is_nullable = 'NO' FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = %s
"""
_UNIQUE = """
SELECT array_agg(a.attname ORDER BY k.ord), pg_get_expr(i.indpred, i.indrelid)
FROM pg_index i
JOIN pg_class t ON t.oid = i.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
WHERE n.nspname = 'public' AND t.relname = %s AND i.indisunique
GROUP BY i.indexrelid, i.indpred, i.indrelid
"""
_FOREIGN = """
SELECT a.attname, ref.relname
FROM pg_constraint c
JOIN pg_class t ON t.oid = c.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
JOIN pg_class ref ON ref.oid = c.confrelid
JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
WHERE c.contype = 'f' AND n.nspname = 'public' AND t.relname = %s
"""


def _violations(conn: psycopg.Connection[Any], table: str) -> list[str]:
    columns = {name: (kind, not_null) for name, kind, not_null in conn.execute(_COLUMNS, (table,))}
    found: list[str] = []
    for name, kind in COMMON.items():
        if name not in columns:
            found.append(f"{table}: no {name} column")
        elif columns[name][0] != kind:
            found.append(f"{table}.{name} is {columns[name][0]}, not {kind}")
    required = ALWAYS_NOT_NULL | (set() if table in OPTIONAL_KEY else KEY_NOT_NULL)
    found += [
        f"{table}.{name} is nullable"
        for name in sorted(required)
        if name in columns and not columns[name][1]
    ]
    keys = [(list(cols), where) for cols, where in conn.execute(_UNIQUE, (table,))]
    expected_where = "(external_id IS NOT NULL)" if table in OPTIONAL_KEY else None
    if (KEY, expected_where) not in keys:
        found.append(f"{table}: no unique key {KEY} (where {expected_where}); has {keys}")
    references = dict(conn.execute(_FOREIGN, (table,)).fetchall())
    found += [
        f"{table}.{column} does not reference {target}"
        for column, target in FOREIGN.items()
        if references.get(column) != target
    ]
    return found


@pytest.mark.req("FR-14.1", "FR-14.3")
@pytest.mark.wp("P0-12")
def test_every_canonical_table_has_ws_connection_external_unique_key(db: DbUrls) -> None:
    """T-P0-12-12
    Tables with `external_id` carry the unique key (workspace_id, connection_id,
    external_id) and the common columns with their types, nullability and references.
    """
    with psycopg.connect(db.libpq(OWNER)) as conn:
        tables = {name for (name,) in conn.execute(_TABLES)} - NOT_CANONICAL.keys()
        assert tables >= CANONICAL, f"missing canonical tables: {CANONICAL - tables}"
        violations = [v for table in sorted(tables) for v in _violations(conn, table)]
    assert violations == []
