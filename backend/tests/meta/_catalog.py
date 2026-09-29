"""Catalog queries shared by the table registry (tests/meta) and the isolation suite
(tests/isolation), P0-06.

Every table in `public` is a tenant table unless the closed allow-list below names it. A
tenant table gets the full checks (base columns, workspace-first indexes, RLS and the
`tenant_isolation` policy); an allow-listed table gets only the checks its kind implies.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Literal

import psycopg

Kind = Literal["global", "root", "own_columns", "append_only"]


@dataclass(frozen=True)
class Allowed:
    """global: no workspace at all, no checks. root: the tenant root, fenced on `id`.
    own_columns: fenced on `workspace_id` with RLS, but not every base column.
    append_only: own columns, fenced on `workspace_id` by policies split by command (a
    SELECT and an INSERT policy for the app role, no UPDATE, DELETE or TRUNCATE grant) and
    the `app.audit_immutable()` trigger (P0-15). The isolation suite's update and delete
    probes need grants these tables deliberately lack, so `_append_only_violations` and
    their own isolation test (test_audit_immutability.py) check them instead.
    `arrives_with` names the work package that creates a table not yet in the schema; the
    stale-entry check skips it until then."""

    kind: Kind
    reason: str
    arrives_with: str | None = None


ALLOW_LIST: dict[str, Allowed] = {
    "deployment_marker": Allowed(
        "global",
        "deployment-level marker (P0-04), read through app.deployment_markers(), no workspace",
    ),
    "workspaces": Allowed("root", "tenant root: its policy is on id instead of workspace_id"),
    "harness_probe": Allowed(
        "global", "harness self-test scratch table (P0-02), test templates only"
    ),
    "outbox": Allowed("own_columns", "workspace-scoped, no version (P0-07)"),
    "idempotency_keys": Allowed(
        "own_columns", "workspace-scoped, no version or deleted_at", arrives_with="P0-10"
    ),
    "audit_log": Allowed("append_only", "append-only, own columns, policies split by command"),
    "audit_anchors": Allowed(
        "append_only", "append-only chain anchors, own columns, policies split by command"
    ),
    "ops_backup_runs": Allowed("global", "deployment-level operations data, no workspace"),
    "ops_status": Allowed("global", "deployment-level operations data, no workspace"),
    "ops_drill_markers": Allowed("global", "deployment-level operations data, no workspace"),
}
# Alembic's version table (and any per-branch variant) is bookkeeping, not data.
ALEMBIC_PREFIX = "alembic_version"

_CREATE = re.compile(r"^CREATE TABLE (?:public\.)?\"?(\w+)\"?", re.MULTILINE)
_DROP = re.compile(r"^DROP TABLE (?:IF EXISTS )?(?:public\.)?\"?(\w+)\"?", re.MULTILINE)
_RENAME = re.compile(r"^ALTER TABLE (?:public\.)?\"?(\w+)\"? RENAME TO \"?(\w+)\"?", re.MULTILINE)
_STATEMENT = re.compile("|".join(p.pattern for p in (_CREATE, _DROP, _RENAME)), re.MULTILINE)


def _is_bookkeeping(table: str) -> bool:
    return table.startswith(ALEMBIC_PREFIX)


def _fenced(tables: set[str]) -> list[str]:
    """Tenant tables plus the fenced allow-list kinds; the tenant root always counts."""
    fenced = {
        t
        for t in tables
        if not _is_bookkeeping(t)
        and (t not in ALLOW_LIST or ALLOW_LIST[t].kind not in ("global", "append_only"))
    }
    fenced |= {name for name, entry in ALLOW_LIST.items() if entry.kind == "root"}
    return sorted(fenced)


def declared_tables() -> set[str]:
    """Tables the migrations create, from `alembic upgrade heads --sql` (offline, no
    database): CREATE, DROP and RENAME statements replayed in revision order."""
    from alembic import command  # noqa: PLC0415

    from tests._pg import alembic_config  # noqa: PLC0415

    buffer = io.StringIO()
    cfg = alembic_config("postgresql+psycopg://offline/tumnis")
    cfg.output_buffer = buffer
    command.upgrade(cfg, "heads", sql=True)
    tables: set[str] = set()
    for match in _STATEMENT.finditer(buffer.getvalue()):
        created, dropped, old, new = match.groups()
        if created:
            tables.add(created)
        elif dropped:
            tables.discard(dropped)
        else:
            tables.discard(old)
            tables.add(new)
    return tables


def declared_fenced_tables() -> list[str]:
    """What the isolation suite parametrizes over, known at collection time."""
    return _fenced(declared_tables())


def public_tables(conn: psycopg.Connection) -> list[str]:
    """Every ordinary or partitioned table in `public`."""
    rows = conn.execute(
        """
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') ORDER BY 1
        """
    ).fetchall()
    return [name for (name,) in rows]


def fenced_tables(conn: psycopg.Connection) -> list[str]:
    """Tables row-level security must fence: tenant tables, own-column tables and the root."""
    existing = set(public_tables(conn))
    return [t for t in _fenced(existing) if t in existing]


def append_only_tables(conn: psycopg.Connection) -> list[str]:
    """The allow-listed append-only tables that exist (P0-15)."""
    existing = set(public_tables(conn))
    return sorted(
        name
        for name, entry in ALLOW_LIST.items()
        if entry.kind == "append_only" and name in existing
    )


def tenant_tables(conn: psycopg.Connection) -> list[str]:
    """Tables that get every check: in `public`, not bookkeeping, not allow-listed."""
    return [t for t in public_tables(conn) if not _is_bookkeeping(t) and t not in ALLOW_LIST]


def tenant_key(conn: psycopg.Connection, table: str) -> str:
    """The column the policy compares with the workspace: `id` for the root."""
    entry = ALLOW_LIST.get(table)
    return "id" if entry is not None and entry.kind == "root" else "workspace_id"


def has_column(conn: psycopg.Connection, table: str, column: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s AND column_name = %s",
        (table, column),
    ).fetchone()
    return row is not None


# name -> (data_type, nullable, column_default)
BASE_COLUMNS: dict[str, tuple[str, bool, str | None]] = {
    "id": ("uuid", False, "uuidv7()"),
    "workspace_id": ("uuid", False, "app.current_workspace_id()"),
    "created_at": ("timestamp with time zone", False, "now()"),
    "updated_at": ("timestamp with time zone", False, "now()"),
    "version": ("integer", False, "1"),
    "deleted_at": ("timestamp with time zone", True, None),
    "created_by": ("text", False, "app.current_actor()"),
}
ACTOR_PATTERN = "^(system|(user|api_key|task_token|device):[0-9a-f-]{36})$"
APP_ROLE = "tumnis_app"
POLICY = "tenant_isolation"

_INDEXES = """
SELECT c.relname AS tbl, i.relname AS idx, ix.indisunique, ix.indisprimary,
       array(SELECT a.attname FROM unnest(ix.indkey) WITH ORDINALITY k(attnum, ord)
             LEFT JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
             ORDER BY k.ord) AS cols
FROM pg_index ix
JOIN pg_class i ON i.oid = ix.indexrelid
JOIN pg_class c ON c.oid = ix.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
"""


def _column_violations(conn: psycopg.Connection, table: str, *, base: bool) -> list[str]:
    found = {
        name: (data_type, nullable == "YES", default)
        for name, data_type, nullable, default in conn.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        )
    }
    expected = BASE_COLUMNS if base else {k: BASE_COLUMNS[k] for k in ("id", "workspace_id")}
    out = []
    for name, spec in expected.items():
        if name not in found:
            out.append(f"columns: {table}: missing base column {name}")
        elif found[name] != spec:
            out.append(f"columns: {table}: {name} is {found[name]}, expected {spec}")
    if not base:
        return out
    checks = [
        definition
        for (definition,) in conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = format('public.%%I', %s::text)::regclass AND contype = 'c'",
            (table,),
        )
    ]
    if not any("created_by" in d and ACTOR_PATTERN in d for d in checks):
        out.append(f"columns: {table}: created_by has no ACTOR_CHECK")
    touch = conn.execute(
        "SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid "
        "JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE t.tgrelid = format('public.%%I', %s::text)::regclass "
        "AND n.nspname = 'app' AND p.proname = 'touch_row' AND NOT t.tgisinternal",
        (table,),
    ).fetchone()
    if touch is None:
        out.append(f"columns: {table}: no app.touch_row() update trigger")
    return out


def _index_violations(conn: psycopg.Connection, tables: set[str]) -> list[str]:
    out = []
    for table, index, unique, primary, cols in conn.execute(_INDEXES):
        if table not in tables or primary or not (unique or len(cols) > 1):
            continue
        if cols[0] != "workspace_id":
            out.append(f"indexes: {table}: {index} {cols} does not lead with workspace_id")
    return sorted(out)


def _rls_violations(conn: psycopg.Connection, table: str, key: str) -> list[str]:
    row = conn.execute(
        "SELECT relrowsecurity FROM pg_class WHERE oid = format('public.%%I', %s::text)::regclass",
        (table,),
    ).fetchone()
    out = []
    if row is None or not row[0]:
        out.append(f"rls: {table}: row-level security is off")
    policy = conn.execute(
        "SELECT permissive, roles, cmd, qual, with_check FROM pg_policies "
        "WHERE schemaname = 'public' AND tablename = %s AND policyname = %s",
        (table, POLICY),
    ).fetchone()
    expected = f"({key} = app.current_workspace_id())"
    if policy is None:
        out.append(f"rls: {table}: no {POLICY} policy")
    elif tuple(policy) != ("PERMISSIVE", [APP_ROLE], "ALL", expected, expected):
        out.append(f"rls: {table}: {POLICY} policy is {tuple(policy)}")
    return out


# pg_trigger.tgtype bits: 8 DELETE, 16 UPDATE, 32 TRUNCATE.
_ROW_CHANGES, _TRUNCATE = 8 | 16, 32


def _append_only_violations(conn: psycopg.Connection, table: str) -> list[str]:
    """RLS on; exactly a SELECT and an INSERT policy for the app role, each fenced on
    workspace_id; no UPDATE, DELETE or TRUNCATE grant; the enabled immutability triggers."""
    out = []
    row = conn.execute(
        "SELECT relrowsecurity FROM pg_class WHERE oid = format('public.%%I', %s::text)::regclass",
        (table,),
    ).fetchone()
    if row is None or not row[0]:
        out.append(f"rls: {table}: row-level security is off")
    expected = "(workspace_id = app.current_workspace_id())"
    policies = sorted(
        (cmd, list(roles), qual, with_check)
        for cmd, roles, qual, with_check in conn.execute(
            "SELECT cmd, roles, qual, with_check FROM pg_policies "
            "WHERE schemaname = 'public' AND tablename = %s",
            (table,),
        )
    )
    wanted = [("INSERT", [APP_ROLE], None, expected), ("SELECT", [APP_ROLE], expected, None)]
    if policies != wanted:
        out.append(f"rls: {table}: policies are {policies}, expected {wanted}")
    for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
        granted = conn.execute(
            "SELECT has_table_privilege(%s, format('public.%%I', %s::text), %s)",
            (APP_ROLE, table, privilege),
        ).fetchone()
        if granted is not None and granted[0]:
            out.append(f"rls: {table}: {APP_ROLE} holds {privilege}")
    covered = 0
    for (tgtype,) in conn.execute(
        "SELECT t.tgtype FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid "
        "JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE t.tgrelid = format('public.%%I', %s::text)::regclass "
        "AND n.nspname = 'app' AND p.proname = 'audit_immutable' AND t.tgenabled <> 'D'",
        (table,),
    ):
        covered |= tgtype & (_ROW_CHANGES | _TRUNCATE)
    if covered != _ROW_CHANGES | _TRUNCATE:
        out.append(f"rls: {table}: app.audit_immutable() does not guard every change")
    return out


def registry_violations(
    conn: psycopg.Connection, kinds: tuple[str, ...] = ("columns", "indexes", "rls")
) -> list[str]:
    """Every way a table in `public` breaks the tenancy rules, as "<kind>: <table>: why"."""
    tables = public_tables(conn)
    full = [t for t in tables if not _is_bookkeeping(t) and t not in ALLOW_LIST]
    own = [t for t in tables if t in ALLOW_LIST and ALLOW_LIST[t].kind == "own_columns"]
    root = [t for t in tables if t in ALLOW_LIST and ALLOW_LIST[t].kind == "root"]
    append_only = [t for t in tables if t in ALLOW_LIST and ALLOW_LIST[t].kind == "append_only"]
    out: list[str] = []
    if "columns" in kinds:
        for table in full:
            out += _column_violations(conn, table, base=True)
        for table in [*own, *append_only]:
            out += _column_violations(conn, table, base=False)
    if "indexes" in kinds:
        out += _index_violations(conn, {*full, *own, *append_only})
    if "rls" in kinds:
        for table in [*full, *own, *root]:
            out += _rls_violations(conn, table, tenant_key(conn, table))
        for table in append_only:
            out += _append_only_violations(conn, table)
    return out


def stale_allow_list_entries(conn: psycopg.Connection) -> list[str]:
    """Allow-list entries naming a table that does not exist, except the ones marked as
    arriving with a later work package."""
    tables = set(public_tables(conn))
    return sorted(
        f"{name}: {entry.reason}"
        for name, entry in ALLOW_LIST.items()
        if entry.arrives_with is None and name not in tables
    )
