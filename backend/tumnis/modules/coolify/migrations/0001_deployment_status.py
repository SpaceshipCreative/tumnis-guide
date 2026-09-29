"""deployment_status (P2-14, FR-12.2): per workspace, the last poll of each linked Coolify
application: its name, last deployment (status, commit, started and finished time) and the
preview links of open pull requests; `checked_at` is the last successful read and `error`
the kind of the last failure (the card shows the status as out of date, not an error).

Which projects link which applications stays in `project_links` (kind `coolify_app`,
P0-17); this table is keyed by the application alone. A tenant table (row-level security,
base columns); `workspace_id` leads the unique key.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "coolify_0001"
down_revision = None
branch_labels = ("coolify",)
depends_on = "auth_0001"
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "deployment_status",
        sa.Column("app_uuid", sa.Text, nullable=False),
        sa.Column("app_name", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=True),  # null: no deployment yet
        sa.Column("deployment_uuid", sa.Text, nullable=True),
        sa.Column("commit", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("previews", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.CheckConstraint(
            "error IS NULL OR error IN ('unavailable', 'rejected')",
            name="ck_deployment_status_error",
        ),
        sa.Index("ux_deployment_status_ws_app", "workspace_id", "app_uuid", unique=True),
    )


def downgrade() -> None:
    drop_tenant_table("deployment_status")
