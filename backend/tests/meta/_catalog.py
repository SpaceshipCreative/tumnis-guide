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

Kind = Literal["global", "root", "own_columns"]


@dataclass(frozen=True)
class Allowed:
    """global: no workspace at all, no checks. root: the tenant root, fenced on `id`.
    own_columns: fenced on `workspace_id` with RLS, but not every base column.
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
    "outbox": Allowed("own_columns", "workspace-scoped, no version", arrives_with="P0-07"),
    "idempotency_keys": Allowed(
        "own_columns", "workspace-scoped, no version or deleted_at", arrives_with="P0-10"
    ),
    "audit_log": Allowed("own_columns", "append-only, own columns", arrives_with="P0-15"),
    "ops_backup_runs": Allowed(
        "global", "deployment-level operations data, no workspace", arrives_with="P0-28"
    ),
    "ops_status": Allowed(
        "global", "deployment-level operations data, no workspace", arrives_with="P0-28"
    ),
    "ops_drill_markers": Allowed(
        "global", "deployment-level operations data, no workspace", arrives_with="P0-28"
    ),
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
        if not _is_bookkeeping(t) and (t not in ALLOW_LIST or ALLOW_LIST[t].kind != "global")
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
    raise NotImplementedError("P0-06 TDD step 3")


def fenced_tables(conn: psycopg.Connection) -> list[str]:
    raise NotImplementedError("P0-06 TDD step 3")


def tenant_tables(conn: psycopg.Connection) -> list[str]:
    raise NotImplementedError("P0-06 TDD step 3")


def tenant_key(conn: psycopg.Connection, table: str) -> str:
    raise NotImplementedError("P0-06 TDD step 3")


def has_column(conn: psycopg.Connection, table: str, column: str) -> bool:
    raise NotImplementedError("P0-06 TDD step 3")


def registry_violations(
    conn: psycopg.Connection, kinds: tuple[str, ...] = ("columns", "indexes", "rls")
) -> list[str]:
    raise NotImplementedError("P0-06 TDD step 3")


def stale_allow_list_entries(conn: psycopg.Connection) -> list[str]:
    raise NotImplementedError("P0-06 TDD step 3")
