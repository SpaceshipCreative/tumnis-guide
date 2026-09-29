"""One way to create a tenant table (P0-06, R-01, ADR-0009).

`create_tenant_table` adds the base columns, a `(workspace_id, id)` index, row-level
security with the `tenant_isolation` policy and the touch trigger, so no tenant table can
skip them; the table registry test (backend/tests/meta/test_table_registry.py) fails for
any table in `public` that was made another way and is not on its allow-list.

Used only inside Alembic revisions (it calls `alembic.op`).
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# The actor that wrote a row: "system" or "<kind>:<uuid>" (tumnis.core.types.ActorRef).
ACTOR_CHECK = r"created_by ~ '^(system|(user|api_key|task_token|device):[0-9a-f-]{36})$'"

APP_ROLE = "tumnis_app"
POLICY = "tenant_isolation"

RLS_SQL = (
    "ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY {POLICY} ON {{table}} AS PERMISSIVE FOR ALL TO {APP_ROLE}"
    " USING ({key} = app.current_workspace_id())"
    " WITH CHECK ({key} = app.current_workspace_id())",
)
TOUCH_SQL = (
    "CREATE TRIGGER {table}_touch BEFORE UPDATE ON {table}"
    " FOR EACH ROW EXECUTE FUNCTION app.touch_row()"
)


def base_columns() -> list[sa.Column[Any]]:
    """The seven base columns, new objects on every call (a Column belongs to one table)."""
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
            server_default=sa.text("app.current_workspace_id()"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_by", sa.Text, nullable=False, server_default=sa.text("app.current_actor()")
        ),
    ]


def enable_tenant_rls(table: str, key: str = "workspace_id") -> None:
    """Row-level security and the `tenant_isolation` policy for the app role. The owner
    owns every table and so bypasses it (no FORCE: migrations and backfills see all rows).
    `key` is `id` only for the tenant root, `workspaces`."""
    for statement in RLS_SQL:
        op.execute(statement.format(table=table, key=key))


def create_tenant_table(
    name: str, *cols: sa.Column[Any] | sa.Constraint | sa.Index, base: bool = True
) -> None:
    """Creates the table with the base columns (or only id + workspace_id when base=False,
    for the allow-listed own-column tables), a (workspace_id, id) index, RLS and, with the
    base columns, ACTOR_CHECK on created_by and the touch trigger."""
    columns = base_columns() if base else base_columns()[:2]
    checks = [sa.CheckConstraint(ACTOR_CHECK, name=f"ck_{name}_created_by")] if base else []
    op.create_table(name, *columns, *cols, *checks)
    op.create_index(f"ix_{name}_ws_id", name, ["workspace_id", "id"])
    enable_tenant_rls(name)
    if base:
        op.execute(TOUCH_SQL.format(table=name))


def drop_tenant_table(name: str) -> None:
    """Drops the table with its policy, trigger and indexes."""
    op.drop_table(name)
