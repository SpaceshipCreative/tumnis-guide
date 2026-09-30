"""Existing folders and the move job (P3-14, FR-15.12).

- folder_moves: one row per move of a project's folder to another location: where from and
  to, its `status` (`copying`, `switched`, `failed`), why it failed (`reason`), how many
  files were verified by hash and whether the old copy is kept (`old_kept`).
- delete_confirmations: the one-time tokens the delete-confirmation dialog is issued for
  deleting an outside file at its source; only the token's sha256 is stored, and a token
  is used once, by the user it was issued to, before it expires.

Chained after knowledge_0007.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "knowledge_0008"
down_revision = "knowledge_0007"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "folder_moves",
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column(
            "from_location",
            UUID(as_uuid=True),
            sa.ForeignKey("storage_locations.id"),
            nullable=False,
        ),
        sa.Column("from_path", sa.Text, nullable=False),
        sa.Column(
            "to_location",
            UUID(as_uuid=True),
            sa.ForeignKey("storage_locations.id"),
            nullable=False,
        ),
        sa.Column("to_path", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'copying'")),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("verified_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("old_kept", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.CheckConstraint(
            "status IN ('copying', 'switched', 'failed')", name="ck_folder_moves_status"
        ),
        sa.Index("ix_folder_moves_ws_project", "workspace_id", "project_id"),
    )
    create_tenant_table(
        "delete_confirmations",
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("token_sha256", sa.LargeBinary, nullable=False),
        sa.Column("issued_to", UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Index("ux_delete_confirmations_token", "workspace_id", "token_sha256", unique=True),
    )


def downgrade() -> None:
    drop_tenant_table("delete_confirmations")
    drop_tenant_table("folder_moves")
