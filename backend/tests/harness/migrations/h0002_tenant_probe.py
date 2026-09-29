"""tenant_probe and tenant_probe_child: tenant tables made with create_tenant_table, for the
registry, isolation and tenancy tests (P0-06; test templates only). The child's NOT NULL
columns without defaults exercise row_factory's type rules and its foreign-key lookup."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "harness_0002"
down_revision = "harness_0001"
branch_labels = None
depends_on = "auth_0001"
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "tenant_probe",
        sa.Column("name", sa.Text, nullable=False),
        sa.Index("uq_tenant_probe_ws_name", "workspace_id", "name", unique=True),
    )
    create_tenant_table(
        "tenant_probe_child",
        sa.Column("probe_id", UUID(as_uuid=True), sa.ForeignKey("tenant_probe.id"), nullable=False),
        sa.Column("amount", sa.Integer, nullable=False),
        sa.Column("flag", sa.Boolean, nullable=False),
        sa.Column("data", JSONB, nullable=False),
        sa.Column("tags", ARRAY(sa.Text), nullable=False),
        sa.Column("due_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Index("ix_tenant_probe_child_ws_probe", "workspace_id", "probe_id"),
    )


def downgrade() -> None:
    drop_tenant_table("tenant_probe_child")
    drop_tenant_table("tenant_probe")
