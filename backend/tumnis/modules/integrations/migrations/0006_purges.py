"""Retention and purge (P3-09, SAAS-2, FR-5.10), expand only.

- purges: one row per purge, a user's (`project`, `connection`) or the retention
  setting's (`retention`): what it targets, the reason (audited), the retention cutoff it
  runs to, the record ids a project purge snapshots when it is asked for (the project's
  archive, which held its own context items, is dropped meanwhile), the counts so far,
  the batches done (the resume key) and its status.
- context_items.target_purged_at: what the item pointed at was purged; the item stays,
  so a task keeps its link and shows that the content went.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "integrations_0006"
down_revision = "integrations_0005"
branch_labels = None
depends_on = None
phase = "expand"

TZ = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    create_tenant_table(
        "purges",
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("cutoff", TZ, nullable=True),
        sa.Column("targets", JSONB, nullable=True),
        sa.Column("counts", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("batches", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'queued'")),
        sa.Column("finished_at", TZ, nullable=True),
        sa.CheckConstraint(
            "scope IN ('retention', 'project', 'connection')", name="ck_purges_scope"
        ),
        sa.CheckConstraint("status IN ('queued', 'running', 'done')", name="ck_purges_status"),
        sa.CheckConstraint("(scope = 'retention') = (target_id IS NULL)", name="ck_purges_target"),
        sa.CheckConstraint("(scope = 'retention') = (cutoff IS NOT NULL)", name="ck_purges_cutoff"),
    )
    op.create_index("ix_purges_ws_status", "purges", ["workspace_id", "status"])
    op.add_column("context_items", sa.Column("target_purged_at", TZ, nullable=True))


def downgrade() -> None:
    op.drop_column("context_items", "target_purged_at")
    drop_tenant_table("purges")
