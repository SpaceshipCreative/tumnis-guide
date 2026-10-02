"""Links between Documents (P3-12, FR-15.10).

- document_links: one row per wikilink, Markdown link or embed in a synced Obsidian note.
  `to_target` is the target as written (a Markdown link's path resolved against the note's
  folder), `to_document_id` the Document it resolves to, NULL while it resolves to none (a
  missing note, or a name several notes share) and set on a later scan when it does.
  `kind` is `link` or `embed`; `heading` and `block` the part after `#` (`#^id` a block).
  Deleting the linking Document deletes its links; deleting the target leaves the link
  unresolved (purge_trash hard-deletes Documents).

Chained after P3-14's knowledge_0009 (folder_moves).
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

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


def downgrade() -> None:
    drop_tenant_table("document_links")
