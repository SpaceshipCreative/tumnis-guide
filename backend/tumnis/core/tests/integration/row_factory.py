"""`minimal_row(conn, table, workspace_id)`: a valid row for any table, built from the
catalog, for tables no seed covers (P0-06).

NOT NULL columns without a default get a value by type (`text` "x", numbers 0, `boolean`
false, `jsonb` {}, arrays {}); a foreign key picks an existing row of the referenced table
in the same workspace, creating one recursively when there is none. A column whose check
constraint the type rule cannot meet gets its value from `COLUMN_VALUES`; a type with no
rule raises, naming the column to add there.

Run it on an owner connection: the owner bypasses row-level security, so it can see and
create rows in any workspace.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

Conn = psycopg.Connection[Any]

# (table, column) -> value, for columns a check constraint pins to a set of values.
COLUMN_VALUES: dict[tuple[str, str], Any] = {
    ("deployment_marker", "env"): "dev",
    ("ops_backup_runs", "repo"): 1,
    ("ops_backup_runs", "type"): "full",
    ("ops_drill_markers", "kind"): "marker",
    ("audit_log", "actor_type"): "system",
    ("memberships", "role"): "owner",
    ("connections", "kind"): "email",
    ("context_items", "target_url"): "https://example.com/context",
    ("project_links", "kind"): "person",
    ("projects", "sort_key"): "a0",  # a valid rank key (core/rank.py); "x" breaks create
    ("provider_configs", "slot"): "decisions",
    ("board_columns", "sort_key"): "a0",
    ("board_columns", "status_map"): "backlog",  # a task_status enum value
    ("tasks", "board_rank"): "a0",
    ("review_items", "kind"): "row_factory",  # ck_review_items_kind
    ("calendar_accounts", "status"): "connected",  # ck_calendar_accounts_status
    ("storage_locations", "kind"): "server_path",  # ck_storage_locations_kind
    ("runners", "name"): "row-factory",  # ck_runners_name
    ("agent_profiles", "name"): "row-factory",  # ck_agent_profiles_name
    ("agent_profiles", "role"): "project",
    ("agent_profiles", "transport"): "daemon",
    ("runs", "kind"): "enrich",
    ("runs", "status"): "queued",
    ("run_events", "kind"): "dispatched",
    ("runner_messages", "direction"): "out",
    ("recurrence_rules", "preset"): "daily",  # ck_recurrence_rules_preset_or_cron
    ("recurrence_rules", "task_template"): Jsonb({"title": "x"}),  # RecurrenceOut.title
    ("search_index", "entity_type"): "task",  # ck_search_index_entity_type
    ("working_hours", "start_local"): time(9, 0),  # ck_working_hours_order
    ("working_hours", "end_local"): time(18, 0),
    ("daily_plans", "source"): "manual",  # ck_daily_plans_source
    ("daily_plans", "trigger"): "manual",  # ck_daily_plans_trigger
    ("daily_plans", "status"): "superseded",  # ck_daily_plans_status
}

_MOMENT = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
_BY_TYPE: dict[str, Any] = {
    "text": "x",
    "character varying": "x",
    "character": "x",
    "citext": "x",
    "smallint": 0,
    "integer": 0,
    "bigint": 0,
    "numeric": 0,
    "real": 0.0,
    "double precision": 0.0,
    "boolean": False,
    "uuid": None,  # a fresh uuid4 per row
    "timestamp with time zone": _MOMENT,
    "timestamp without time zone": _MOMENT.replace(tzinfo=None),
    "date": date(2026, 3, 9),
    "time without time zone": time(0),
    "interval": timedelta(0),
    "bytea": b"",
}

_COLUMNS = """
SELECT column_name, data_type, udt_name, is_nullable = 'YES', column_default,
       is_identity = 'YES' OR is_generated = 'ALWAYS'
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = %s
ORDER BY ordinal_position
"""
_FOREIGN_KEYS = """
SELECT a.attname, ref.relname, ra.attname
FROM pg_constraint c
JOIN pg_class t ON t.oid = c.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
JOIN pg_class ref ON ref.oid = c.confrelid
JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
JOIN pg_attribute ra ON ra.attrelid = c.confrelid AND ra.attnum = c.confkey[1]
WHERE c.contype = 'f' AND n.nspname = 'public' AND t.relname = %s
  AND cardinality(c.conkey) = 1
"""


def _type_value(table: str, column: str, data_type: str, udt: str) -> Any:
    if data_type == "ARRAY":
        return []
    if data_type in {"json", "jsonb"}:
        return Jsonb({})
    key = udt if data_type == "USER-DEFINED" else data_type
    if key not in _BY_TYPE:
        raise NotImplementedError(
            f"row_factory: no value for {table}.{column} ({key}); add it to COLUMN_VALUES"
        )
    value = _BY_TYPE[key]
    return uuid4() if key == "uuid" else value


def _has_workspace(conn: Conn, table: str) -> bool:
    return any(name == "workspace_id" for name, *_ in conn.execute(_COLUMNS, (table,)))


def _parent(
    conn: Conn, table: str, column: str, workspace_id: UUID | None, seen: frozenset[str]
) -> Any:
    """An existing referenced value in the same workspace, or a new parent row's."""
    if table == "workspaces" and column == "id" and workspace_id is not None:
        return workspace_id
    where, params = sql.SQL(""), []
    if workspace_id is not None and _has_workspace(conn, table):
        where, params = sql.SQL(" WHERE workspace_id = %s"), [workspace_id]
    query = sql.SQL("SELECT {} FROM {}{} LIMIT 1").format(
        sql.Identifier(column), sql.Identifier(table), where
    )
    row = conn.execute(query, params).fetchone()
    if row is not None:
        return row[0]
    created = insert_row(conn, table, _build(conn, table, workspace_id, seen | {table}))
    if created is None:
        raise RuntimeError(f"row_factory: could not create a {table} row for the foreign key")
    return created[column]


def _build(
    conn: Conn, table: str, workspace_id: UUID | None, seen: frozenset[str]
) -> dict[str, Any]:
    foreign = {col: (ref, ref_col) for col, ref, ref_col in conn.execute(_FOREIGN_KEYS, (table,))}
    row: dict[str, Any] = {}
    for column, data_type, udt, nullable, default, generated in conn.execute(_COLUMNS, (table,)):
        if generated:
            continue
        if column == "workspace_id":
            if workspace_id is None:
                raise ValueError(f"row_factory: {table} is a tenant table; pass a workspace")
            row[column] = workspace_id
        elif (table, column) in COLUMN_VALUES:
            row[column] = COLUMN_VALUES[table, column]
        elif nullable or default is not None:
            continue
        elif column in foreign:
            ref, ref_col = foreign[column]
            if ref in seen:
                raise RuntimeError(f"row_factory: foreign-key cycle through {ref}")
            row[column] = _parent(conn, ref, ref_col, workspace_id, seen)
        else:
            row[column] = _type_value(table, column, data_type, udt)
    return row


def minimal_row(conn: Conn, table: str, workspace_id: UUID | None) -> dict[str, Any]:
    """Column values for one valid row of `table` in `workspace_id` (not inserted; foreign
    key parents it needs are inserted). Global tables take `workspace_id=None`."""
    return _build(conn, table, workspace_id, frozenset({table}))


def insert_row(conn: Conn, table: str, values: dict[str, Any]) -> dict[str, Any] | None:
    """INSERT ... ON CONFLICT DO NOTHING RETURNING *; None when the row already existed."""
    if values:
        query = sql.SQL(
            "INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING RETURNING *"
        ).format(
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, values)),
            sql.SQL(", ").join(sql.Placeholder() * len(values)),
        )
    else:
        query = sql.SQL("INSERT INTO {} DEFAULT VALUES ON CONFLICT DO NOTHING RETURNING *").format(
            sql.Identifier(table)
        )
    cursor = conn.execute(query, list(values.values()))
    row = cursor.fetchone()
    if row is None:
        return None
    return {col.name: value for col, value in zip(cursor.description or (), row, strict=True)}


def fill_every_table(conn: Conn) -> list[str]:
    """One minimal row in every table in `public` but Alembic's, all in one new workspace
    when `workspaces` exists; returns the tables that received a row."""
    tables = [
        name
        for (name,) in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"
        )
        if not name.startswith("alembic_version")
    ]
    filled: list[str] = []
    workspace_id = None
    if "workspaces" in tables:
        created = insert_row(conn, "workspaces", minimal_row(conn, "workspaces", None))
        assert created is not None  # a new id never conflicts
        workspace_id = created["id"]
        filled.append("workspaces")
    for table in tables:
        if table == "workspaces":
            continue
        ws = workspace_id if _has_workspace(conn, table) else None
        insert_row(conn, table, minimal_row(conn, table, ws))
        filled.append(table)
    return filled
