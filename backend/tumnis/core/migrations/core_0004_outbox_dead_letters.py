"""outbox and dead_letters, and the two relay functions (P0-07, ADR-0011, ADR-0009).

- outbox: workspace-scoped with its own columns (`create_tenant_table(..., base=False)`
  keeps `id`, `workspace_id`, the (workspace_id, id) index and the tenant policy; the table
  registry allow-lists it). `emit()` inserts a row and NOTIFYs `outbox` in the writer's
  transaction; the relay marks it sent. `ix_outbox_unsent` is the relay's scan.
- dead_letters: a full tenant table, one row per (workspace, event, subscriber) whose
  delivery ran out of attempts; list, retry and discard live in tumnis.core.deadletter.
- The relay reads across workspaces, which row-level security forbids the app role. Two
  narrow SECURITY DEFINER functions, owned by tumnis_owner (not subject to the policy), are
  the only exception, pinned by tests/meta/test_security_definer.py. Row locks taken by
  `FOR UPDATE SKIP LOCKED` inside the function last until the caller's transaction ends, so
  two relays never claim the same row.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "core_0004_outbox"
down_revision = "core_0006_audit"
branch_labels = None
depends_on = "auth_0001"  # outbox.workspace_id and dead_letters.workspace_id -> workspaces
phase = "expand"

STATUSES = ("open", "retrying", "resolved", "discarded")

FUNCTIONS = (
    """
    CREATE FUNCTION app.outbox_claim(p_limit integer)
      RETURNS SETOF public.outbox
      LANGUAGE sql VOLATILE SECURITY DEFINER
      SET search_path = pg_catalog, public
      AS $$
        SELECT * FROM public.outbox WHERE sent_at IS NULL
        ORDER BY id LIMIT p_limit FOR UPDATE SKIP LOCKED
      $$
    """,
    """
    CREATE FUNCTION app.outbox_mark_sent(p_ids uuid[])
      RETURNS integer
      LANGUAGE sql VOLATILE SECURITY DEFINER
      SET search_path = pg_catalog, public
      AS $$
        WITH u AS (
          UPDATE public.outbox SET sent_at = now()
          WHERE id = ANY(p_ids) AND sent_at IS NULL
          RETURNING 1
        )
        SELECT count(*)::integer FROM u
      $$
    """,
    "REVOKE ALL ON FUNCTION app.outbox_claim(integer), app.outbox_mark_sent(uuid[]) FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION app.outbox_claim(integer), app.outbox_mark_sent(uuid[])"
    " TO tumnis_app",
)


def upgrade() -> None:
    create_tenant_table(
        "outbox",
        sa.Column(
            "event_id", UUID(as_uuid=True), nullable=False, server_default=sa.text("uuidv7()")
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("schema_version", sa.Integer, nullable=False),
        sa.Column("actor", sa.Text, nullable=False, server_default=sa.text("app.current_actor()")),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("trace_context", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
        base=False,
    )
    op.create_index("uq_outbox_ws_event", "outbox", ["workspace_id", "event_id"], unique=True)
    op.create_index(
        "ix_outbox_unsent", "outbox", ["id"], postgresql_where=sa.text("sent_at IS NULL")
    )

    status_list = ", ".join(f"'{s}'" for s in STATUSES)
    create_tenant_table(
        "dead_letters",
        sa.Column("event_id", UUID(as_uuid=True), nullable=False),
        sa.Column("subscriber", sa.Text, nullable=False),
        sa.Column("event_name", sa.Text, nullable=False),
        sa.Column("envelope", JSONB, nullable=False),
        sa.Column("error", sa.Text, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("retries", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("last_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'open'")),
        sa.CheckConstraint(f"status IN ({status_list})", name="ck_dead_letters_status"),
    )
    op.create_index(
        "uq_dead_letters_ws_event_sub",
        "dead_letters",
        ["workspace_id", "event_id", "subscriber"],
        unique=True,
    )
    op.create_index("ix_dead_letters_ws_status", "dead_letters", ["workspace_id", "status", "id"])
    for statement in FUNCTIONS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP FUNCTION app.outbox_mark_sent(uuid[])")
    op.execute("DROP FUNCTION app.outbox_claim(integer)")
    drop_tenant_table("dead_letters")
    drop_tenant_table("outbox")
