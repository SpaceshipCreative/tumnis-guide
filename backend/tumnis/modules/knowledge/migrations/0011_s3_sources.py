"""S3 buckets as a linked source (P3-13, FR-15.11).

- s3_sources: one per S3 connection (`connections` row, provider `s3`): the endpoint,
  region, bucket and addressing style in clear; the access key and secret sealed with the
  workspace data key in `config_enc` (aad `s3_sources:<id>`); the prefix-to-project map
  (`prefixes`, a JSON list of {prefix, project_id}); whether its files are trusted; the
  key's checked capabilities; the SHA-256 of the MinIO webhook token (the token itself is
  shown once and never stored); and when its last sync finished.
- folder_files gains `connection_id`: a linked source's objects are recorded like a
  location's files, keyed by (workspace, connection, key) instead of (workspace,
  location, path). Exactly one of the two is set (checked NOT VALID: every existing row
  has a location and no connection).
- app.s3_source_webhook(connection_id): the MinIO webhook route has no workspace until it
  finds the connection's, so this SECURITY DEFINER reader returns the workspace and the
  token's hash for one live source, nothing else.

Numbered knowledge_0011 (the coordinator reserved 0008 to 0010 for P3-14 and P3-12) and
chained after knowledge_0008, main's head now; whichever PR merges later
re-chains to main's head.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "knowledge_0011"
down_revision = "knowledge_0008"
branch_labels = None
depends_on = None
phase = "expand"

APP_ROLE = "tumnis_app"
SIGNATURE = "app.s3_source_webhook(uuid)"
FUNCTION = """
CREATE FUNCTION app.s3_source_webhook(p_connection_id uuid)
RETURNS TABLE (workspace_id uuid, webhook_token_sha256 bytea)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT s.workspace_id, s.webhook_token_sha256
  FROM public.s3_sources s
  WHERE s.connection_id = p_connection_id AND s.deleted_at IS NULL
$$
"""


def upgrade() -> None:
    create_tenant_table(
        "s3_sources",
        sa.Column(
            "connection_id", UUID(as_uuid=True), sa.ForeignKey("connections.id"), nullable=False
        ),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("endpoint", sa.Text, nullable=False),
        sa.Column("region", sa.Text, nullable=False),
        sa.Column("bucket", sa.Text, nullable=False),
        sa.Column("path_style", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("config_enc", BYTEA, nullable=False),
        sa.Column("prefixes", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("trusted", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("capabilities", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("webhook_token_sha256", BYTEA, nullable=False),
        sa.Column("last_sync_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("provider IN ('minio', 'b2', 'other')", name="ck_s3_sources_provider"),
        sa.Index("ux_s3_sources_ws_connection", "workspace_id", "connection_id", unique=True),
    )
    op.add_column("folder_files", sa.Column("connection_id", UUID(as_uuid=True), nullable=True))
    op.execute(
        "ALTER TABLE folder_files ADD CONSTRAINT fk_folder_files_connection_id_connections"
        " FOREIGN KEY (connection_id) REFERENCES connections (id) NOT VALID"
    )
    op.alter_column("folder_files", "location_id", nullable=True)
    op.execute(
        "ALTER TABLE folder_files ADD CONSTRAINT ck_folder_files_location_or_connection"
        " CHECK ((location_id IS NULL) <> (connection_id IS NULL)) NOT VALID"
    )
    op.create_index(
        "ux_folder_files_ws_connection_path",
        "folder_files",
        ["workspace_id", "connection_id", "path"],
        unique=True,
        postgresql_where=sa.text("connection_id IS NOT NULL"),
    )
    op.execute(FUNCTION)
    op.execute(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION {SIGNATURE}")
    op.drop_index("ux_folder_files_ws_connection_path", table_name="folder_files")
    op.execute("DELETE FROM folder_files WHERE connection_id IS NOT NULL")
    op.drop_constraint("ck_folder_files_location_or_connection", "folder_files", type_="check")
    op.alter_column("folder_files", "location_id", nullable=False)
    op.drop_constraint(
        "fk_folder_files_connection_id_connections", "folder_files", type_="foreignkey"
    )
    op.drop_column("folder_files", "connection_id")
    drop_tenant_table("s3_sources")
