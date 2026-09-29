"""search_index (P0-20, FR-3.9): one full-text row per task and project, fed by events.

- `tsv` is a stored generated column: the title in the english (stemmed, weight A) and
  simple (whole lexemes, weight B) configurations, then the body likewise (C, D). A literal
  regconfig makes `to_tsvector` immutable, so it may generate a column.
- `source_updated_at` is when the source row changed (the recency input and the guard
  against a late event); the base `updated_at` is row maintenance.
- `ux_search_ws_entity` is the upsert key; `ix_search_tsv` is the GIN index the `@@`
  match uses.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "search_0001"
down_revision = None
branch_labels = ("search",)
depends_on = "auth_0001"
phase = "expand"

TSV = (
    "setweight(to_tsvector('english'::regconfig, title), 'A') || "
    "setweight(to_tsvector('simple'::regconfig, title), 'B') || "
    "setweight(to_tsvector('english'::regconfig, body), 'C') || "
    "setweight(to_tsvector('simple'::regconfig, body), 'D')"
)


def upgrade() -> None:
    create_tenant_table(
        "search_index",
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("source_updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("tsv", TSVECTOR, sa.Computed(TSV, persisted=True)),
        sa.CheckConstraint(
            "entity_type IN ('task', 'project')", name="ck_search_index_entity_type"
        ),
        sa.Index("ux_search_ws_entity", "workspace_id", "entity_type", "entity_id", unique=True),
        sa.Index("ix_search_tsv", "tsv", postgresql_using="gin"),
    )


def downgrade() -> None:
    drop_tenant_table("search_index")
