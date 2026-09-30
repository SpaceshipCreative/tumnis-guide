"""webhook_deliveries (P2-13, SEC-5).

One row per accepted `X-GitHub-Delivery`: the unique `(workspace_id, delivery_id)` key is
how the signed webhook endpoint refuses a replayed delivery (409 `duplicate_delivery`).
A tenant table (row-level security, base columns); `workspace_id` leads the unique key.

The pull request status itself needs no table of its own: it is kept in the integrations
module's `artifacts` (`state`, and `checks.pr_status`) and `raw_payloads`.
"""

import sqlalchemy as sa

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "github_0001"
down_revision = None
branch_labels = ("github",)
depends_on = "auth_0001"
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "webhook_deliveries",
        sa.Column("delivery_id", sa.Text, nullable=False),
        sa.Column("received_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(delivery_id) BETWEEN 1 AND 200", name="ck_webhook_deliveries_id"
        ),
        sa.Index("ux_webhook_deliveries_ws_delivery", "workspace_id", "delivery_id", unique=True),
    )


def downgrade() -> None:
    drop_tenant_table("webhook_deliveries")
