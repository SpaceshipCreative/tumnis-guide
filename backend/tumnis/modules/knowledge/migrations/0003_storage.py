"""Storage locations, project folders, document versions and queued writes (P1-14,
FR-15.7, FR-15.12).

- storage_locations: where a workspace's knowledge-base files live (a server path or an
  S3 bucket/prefix). `config_enc` holds the S3 endpoint and keys sealed with the
  workspace data key. One live default per workspace (`ux_storage_locations_one_default`);
  names are unique per workspace, ignoring case, among live rows.
- project_folders: a project's folder on a location, one per project.
- document_versions: snapshots of a text document's body; P1-14 needs the table as the
  target of a queued write, P1-17 extends it (history, restore).
- pending_writes: note writes queued while their location is offline, drained in
  insertion order when it comes back.

`documents.storage_location_id` gets its foreign key now that the table exists, added NOT
VALID like knowledge_0002's project key (no scan of `documents` inside the migration).
All four are tenant tables; `workspace_id` leads every multi-column index.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "knowledge_0003"
down_revision = "knowledge_0002"
branch_labels = None
depends_on = "projects_0001"
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "storage_locations",
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("root", sa.Text, nullable=False),
        sa.Column("config_enc", sa.LargeBinary, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'online'")),
        sa.Column("status_reason", sa.Text, nullable=True),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("capabilities", JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.CheckConstraint(
            "kind IN ('server_path', 's3', 'sftp')", name="ck_storage_locations_kind"
        ),
        sa.CheckConstraint("status IN ('online', 'offline')", name="ck_storage_locations_status"),
        sa.Index(
            "ux_storage_locations_one_default",
            "workspace_id",
            unique=True,
            postgresql_where=sa.text("is_default AND deleted_at IS NULL"),
        ),
        sa.Index(
            "ux_storage_locations_ws_name",
            "workspace_id",
            sa.text("lower(name)"),
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )
    create_tenant_table(
        "project_folders",
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column(
            "location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("storage_locations.id"),
            nullable=False,
        ),
        sa.Column("root_path", sa.Text, nullable=False),
        sa.Column("mode", sa.Text, nullable=False, server_default=sa.text("'tumnis_made'")),
        sa.Column("backup_opt_in", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.CheckConstraint("mode IN ('tumnis_made', 'existing')", name="ck_project_folders_mode"),
        sa.Index("ux_project_folders_ws_project", "workspace_id", "project_id", unique=True),
        sa.Index("ix_project_folders_ws_location", "workspace_id", "location_id"),
    )
    create_tenant_table(
        "document_versions",
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("content_hash", sa.LargeBinary, nullable=False),
        sa.Column("body_md", sa.Text, nullable=False),
        sa.Column("size", sa.BigInteger, nullable=False),
        sa.Index(
            "ux_document_versions_ws_document_no",
            "workspace_id",
            "document_id",
            "version_no",
            unique=True,
        ),
    )
    create_tenant_table(
        "pending_writes",
        sa.Column(
            "location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("storage_locations.id"),
            nullable=False,
        ),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column(
            "document_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column("if_match", sa.Text, nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Index("ix_pending_writes_ws_location", "workspace_id", "location_id", "id"),
    )
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT fk_documents_storage_location_id"
        " FOREIGN KEY (storage_location_id) REFERENCES storage_locations (id) NOT VALID"
    )


def downgrade() -> None:
    op.drop_constraint("fk_documents_storage_location_id", "documents", type_="foreignkey")
    drop_tenant_table("pending_writes")
    drop_tenant_table("document_versions")
    drop_tenant_table("project_folders")
    drop_tenant_table("storage_locations")
