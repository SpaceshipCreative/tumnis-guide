"""usage_counters and usage_ledger (P0-21, Hosted readiness).

- usage_counters: one row per (workspace, UTC day, counter) with its running value.
- usage_ledger: one row per (workspace, event, counter), written in the same statement
  that raises the counter; its unique key is the guard against double delivery.

Both are tenant tables (row-level security, base columns); `workspace_id` leads both
unique keys.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "usage_0001"
down_revision = None
branch_labels = ("usage",)
depends_on = "auth_0001"
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "usage_counters",
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("counter", sa.Text, nullable=False),
        sa.Column("value", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint("value >= 0", name="ck_usage_counters_value"),
        sa.Index("ux_usage_counters", "workspace_id", "day", "counter", unique=True),
    )
    create_tenant_table(
        "usage_ledger",
        sa.Column("event_id", UUID(as_uuid=True), nullable=False),
        sa.Column("counter", sa.Text, nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("amount", sa.BigInteger, nullable=False),
        sa.CheckConstraint("amount >= 0", name="ck_usage_ledger_amount"),
        sa.Index("ux_usage_ledger", "workspace_id", "event_id", "counter", unique=True),
    )


def downgrade() -> None:
    drop_tenant_table("usage_ledger")
    drop_tenant_table("usage_counters")
