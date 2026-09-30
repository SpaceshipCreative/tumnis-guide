"""Folder sync records (P1-15, FR-15.12).

- folder_files: one row per file the sync engine knows on a location, the state at the
  last sync (size, mtime, etag, content hash as sha256 hex), where it came from (`origin`:
  `tumnis` wrote it, or it came from outside) and the Document it is linked to, with the
  Document version last written or read (`synced_version`). `delete_confirmed` records the
  user's in-app confirmation to delete an outside file at its source; `last_op` the last
  action applied. Unique per (workspace, location, path).
- storage_locations.last_sync_at: when the location's last folder sync finished.

Chained after knowledge_0003. P1-16's extraction revision, knowledge_0006, chains after this
one (it was planned as knowledge_0004 on knowledge_0003; there is no knowledge_0004).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "knowledge_0005"
down_revision = "knowledge_0003"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "folder_files",
        sa.Column(
            "location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("storage_locations.id"),
            nullable=False,
        ),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("size", sa.BigInteger, nullable=False),
        sa.Column("mtime", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("etag", sa.Text, nullable=False),
        sa.Column("origin", sa.Text, nullable=False),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=True),
        sa.Column("synced_version", sa.Integer, nullable=True),
        sa.Column("delete_confirmed", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("last_op", sa.Text, nullable=True),
        sa.CheckConstraint("origin IN ('tumnis', 'external')", name="ck_folder_files_origin"),
        sa.Index(
            "ux_folder_files_ws_location_path", "workspace_id", "location_id", "path", unique=True
        ),
        sa.Index("ix_folder_files_ws_document", "workspace_id", "document_id"),
    )
    op.add_column(
        "storage_locations", sa.Column("last_sync_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("storage_locations", "last_sync_at")
    drop_tenant_table("folder_files")
