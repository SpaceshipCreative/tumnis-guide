"""The `documents` table with the canonical columns (P0-12, R-15). P0-17 adds `role` and
`body_md` in `knowledge_0002`. Text entries and uploads have no connection or external id:
those columns (and fetched_at) are nullable here, and the canonical unique key is partial
(`WHERE external_id IS NOT NULL`). `project_id` and `storage_location_id` get their foreign
keys when projects (P0-17) and storage locations (P1-14) exist."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from tumnis.core.migration_helpers import (
    canonical_columns,
    create_tenant_table,
    drop_tenant_table,
)

revision = "knowledge_0001"
down_revision = None
branch_labels = ("knowledge",)
depends_on = "integrations_0001"
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "documents",
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("trust", sa.Text, nullable=False, server_default=sa.text("'untrusted'")),
        sa.Column("storage_location_id", UUID(as_uuid=True), nullable=True),
        sa.Column("path", sa.Text, nullable=True),
        sa.Column("source_revision", sa.Text, nullable=True),
        sa.Column("pinned", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("tags", ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")),
        *canonical_columns("documents", tainted=True, optional_key=True),
        sa.CheckConstraint("trust IN ('trusted', 'untrusted')", name="ck_documents_trust"),
    )


def downgrade() -> None:
    drop_tenant_table("documents")
