"""Upload safety and extraction (P1-16, SEC-10, FR-15.2).

- documents: `status` (pending_scan, extracting, ready, quarantined, failed; existing rows
  and text entries are `ready`), `status_reason` (a refusal code such as `type_mismatch`, or
  the signature scanners named) and `current_version_id` (the version search reads).
- document_versions: the same status pair, the name a file arrived under (`source_name`),
  its sniffed `mime`, and the compressed Docling document (`docling_json`). `body_md` stays
  NOT NULL: an upload's version holds '' until its Markdown export arrives (dropping NOT NULL
  would hand the previous release NULLs it never expected; squawk's ban-drop-not-null).
- extraction_artifacts: what each pipeline step hands the next (the converted document, its
  Markdown, the vision pages, the chunks), one row per (version, stage). Large outputs never
  pass through DBOS step results.
- chunks: the searchable pieces of a version, with their heading path and exact pages.
  `tsv` is generated from the heading path and the text through `chunk_tsv`, an immutable
  wrapper (array_to_string is only stable, and a generated column needs an immutable
  expression); P1-17's search reads it.

`documents.current_version_id` gets its foreign key NOT VALID, like knowledge_0003's keys, and
both status checks are added NOT VALID too (squawk's constraint-missing-not-valid): no scan
runs inside the migration's transaction, every new or changed row is checked, and every
existing row already holds the new columns' default `ready`.

Chained after P1-15's knowledge_0005 (it was knowledge_0004 on knowledge_0003 until both
work packages landed).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "knowledge_0006"
down_revision = "knowledge_0005"
branch_labels = None
depends_on = None
phase = "expand"

STATUSES = "'pending_scan', 'extracting', 'ready', 'quarantined', 'failed'"
CHUNK_TSV = "to_tsvector('english', coalesce(array_to_string(h, ' '), '') || ' ' || t)"


def _check_not_valid(table: str, name: str) -> None:
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK (status IN ({STATUSES})) NOT VALID"
    )


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'ready'")),
    )
    op.add_column("documents", sa.Column("status_reason", sa.Text, nullable=True))
    op.add_column("documents", sa.Column("current_version_id", UUID(as_uuid=True), nullable=True))
    _check_not_valid("documents", "ck_documents_status")
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT fk_documents_current_version_id"
        " FOREIGN KEY (current_version_id) REFERENCES document_versions (id) NOT VALID"
    )
    op.add_column(
        "document_versions",
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'ready'")),
    )
    op.add_column("document_versions", sa.Column("status_reason", sa.Text, nullable=True))
    op.add_column("document_versions", sa.Column("source_name", sa.Text, nullable=True))
    op.add_column("document_versions", sa.Column("mime", sa.Text, nullable=True))
    op.add_column("document_versions", sa.Column("docling_json", sa.LargeBinary, nullable=True))
    _check_not_valid("document_versions", "ck_document_versions_status")

    create_tenant_table(
        "extraction_artifacts",
        sa.Column(
            "version_id", UUID(as_uuid=True), sa.ForeignKey("document_versions.id"), nullable=False
        ),
        sa.Column("stage", sa.Text, nullable=False),
        sa.Column("data", sa.LargeBinary, nullable=False),
        sa.Index(
            "ux_extraction_artifacts_ws_version_stage",
            "workspace_id",
            "version_id",
            "stage",
            unique=True,
        ),
    )

    op.execute(
        "CREATE FUNCTION chunk_tsv(h text[], t text) RETURNS tsvector"
        f" LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT {CHUNK_TSV} $$"
    )
    # Functions are private by default (02-database.sql); the app role writes `chunks`,
    # whose generated column calls this.
    op.execute("GRANT EXECUTE ON FUNCTION chunk_tsv(text[], text) TO tumnis_app")
    create_tenant_table(
        "chunks",
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column(
            "document_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("context_text", sa.Text, nullable=False),
        sa.Column("heading_path", ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("page_from", sa.Integer, nullable=True),
        sa.Column("page_to", sa.Integer, nullable=True),
        sa.Column("extractor", sa.Text, nullable=False, server_default=sa.text("'docling'")),
        sa.Column(
            "tsv",
            TSVECTOR,
            sa.Computed("chunk_tsv(heading_path, text)", persisted=True),
            nullable=False,
        ),
        sa.CheckConstraint("extractor IN ('docling', 'vlm')", name="ck_chunks_extractor"),
        sa.Index(
            "ux_chunks_ws_version_ordinal",
            "workspace_id",
            "document_version_id",
            "ordinal",
            unique=True,
        ),
        sa.Index("ix_chunks_ws_document", "workspace_id", "document_id"),
        sa.Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )


def downgrade() -> None:
    drop_tenant_table("chunks")
    op.execute("DROP FUNCTION chunk_tsv(text[], text)")
    drop_tenant_table("extraction_artifacts")
    op.drop_constraint("ck_document_versions_status", "document_versions", type_="check")
    for column in ("docling_json", "mime", "source_name", "status_reason", "status"):
        op.drop_column("document_versions", column)
    op.drop_constraint("fk_documents_current_version_id", "documents", type_="foreignkey")
    op.drop_constraint("ck_documents_status", "documents", type_="check")
    for column in ("current_version_id", "status_reason", "status"):
        op.drop_column("documents", column)
