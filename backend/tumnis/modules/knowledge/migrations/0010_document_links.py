"""Links between Documents, and Obsidian vault settings (P3-12, FR-15.10).

- document_links: one row per wikilink, Markdown link or embed in a synced Obsidian note.
  `to_target` is the target as written (a Markdown link's path resolved against the note's
  folder), `to_document_id` the Document it resolves to, NULL while it resolves to none (a
  missing note, or a name several notes share) and set on a later scan when it does.
  `kind` is `link` or `embed`; `heading` and `block` the part after `#` (`#^id` a block).
  Deleting the linking Document deletes its links; deleting the target leaves the link
  unresolved (purge_trash hard-deletes Documents).

- obsidian_vaults: one per Obsidian vault connection (a `connections` row, kind
  `knowledge`, provider `obsidian`, that the knowledge module makes, outside P3-02's
  framework tick): `mode` folder|git, the folder path or the Git remote and branch, the
  mapping (folders, frontmatter key, tag prefix, unmapped handling, clippings folder, extra
  excludes) as JSON, the pinned known_hosts line (Git; confirmed by the person, never
  accepted automatically: decision 82), the deploy key's public half (the private half is
  sealed in the connection's `credentials_enc`), and the vault's own status (`pending`,
  `connecting`, `ok`, `error` with `last_error`) and `last_sync_at`, which Settings reads
  (P3-02's connection reads list framework providers only).

Chained after P3-14's knowledge_0009 (folder_moves).
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "knowledge_0010"
down_revision = "knowledge_0009"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "document_links",
        sa.Column(
            "from_document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("to_target", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("heading", sa.Text, nullable=True),
        sa.Column("block", sa.Text, nullable=True),
        sa.CheckConstraint("kind IN ('link', 'embed')", name="ck_document_links_kind"),
        sa.Index("ix_document_links_ws_from", "workspace_id", "from_document_id"),
        sa.Index("ix_document_links_ws_to", "workspace_id", "to_document_id"),
    )
    create_tenant_table(
        "obsidian_vaults",
        sa.Column(
            "connection_id", UUID(as_uuid=True), sa.ForeignKey("connections.id"), nullable=False
        ),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column("folder_path", sa.Text, nullable=True),
        sa.Column("remote", sa.Text, nullable=True),
        sa.Column("branch", sa.Text, nullable=False, server_default=sa.text("'main'")),
        sa.Column("mapping", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("known_hosts", sa.Text, nullable=True),
        sa.Column("deploy_public_key", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("last_sync_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("mode IN ('folder', 'git')", name="ck_obsidian_vaults_mode"),
        sa.CheckConstraint(
            "status IN ('pending', 'connecting', 'ok', 'error')",
            name="ck_obsidian_vaults_status",
        ),
        sa.Index("ux_obsidian_vaults_ws_connection", "workspace_id", "connection_id", unique=True),
    )


def downgrade() -> None:
    drop_tenant_table("obsidian_vaults")
    drop_tenant_table("document_links")
