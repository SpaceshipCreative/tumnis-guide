"""Embeddings and hybrid search (P3-10, FR-15.3, FR-11.10, R-37).

- The `vector` extension must exist: initdb (deploy/postgres/initdb/02-database.sql)
  creates it as the superuser in every database; the owner role cannot, so this revision
  only asserts it.
- embedding_models: each embedding model a workspace has used, its dimension, provider and
  state (`building` while `reembed_all` fills its rows, `active`, `retired`), and the name
  of its partial HNSW index once that exists.
- embeddings: one vector per (chunk, model). The column is an untyped `vector`, so models
  of different dimensions share it; each model gets a partial expression HNSW index cast
  to its own dimension (pgvector README: "Indexing" for mixed dimensions), created here
  for the default model and by `tumnis embeddings index <model>` for later ones.
  `project_id` is copied from the document for filtering. A chunk's rows go with it
  (chunks are replaced, not updated, on re-extraction).

HNSW build parameters are pgvector's defaults, stated explicitly (m 16, ef_construction 64).
The index name is `knowledge.rules.hnsw_index_name("BAAI/bge-m3")`, written out here
because a revision imports nothing from the app at run time.

Chained after knowledge_0006 (main's knowledge head). P3-14 (#107, #113) also adds
knowledge revisions; whichever merges later re-chains.
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "knowledge_0007"
down_revision = "knowledge_0006"
branch_labels = None
depends_on = None
phase = "expand"

DEFAULT_MODEL = "BAAI/bge-m3"
DEFAULT_DIMS = 1024
DEFAULT_INDEX = "embeddings_hnsw_baai_bge_m3"


class _Vector(sa.types.UserDefinedType[Any]):
    cache_ok = True

    def get_col_spec(self, **_kw: Any) -> str:
        return "vector"


def upgrade() -> None:
    # Asserted in SQL, so the offline (--sql) rendering of the chain works too.
    op.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN"
        " RAISE EXCEPTION 'the pgvector extension is missing: run deploy/postgres/initdb"
        " as the superuser'; END IF; END $$"
    )
    create_tenant_table(
        "embedding_models",
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("dims", sa.Integer, nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("index_name", sa.Text, nullable=True),
        sa.CheckConstraint("dims BETWEEN 1 AND 2000", name="ck_embedding_models_dims"),
        sa.CheckConstraint(
            "status IN ('building', 'active', 'retired')", name="ck_embedding_models_status"
        ),
        sa.Index("ux_embedding_models_ws_model", "workspace_id", "model", unique=True),
    )
    create_tenant_table(
        "embeddings",
        sa.Column(
            "chunk_id",
            UUID(as_uuid=True),
            sa.ForeignKey("chunks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("embedding", _Vector(), nullable=False),
        sa.Index("ux_embeddings_ws_chunk_model", "workspace_id", "chunk_id", "model", unique=True),
        sa.Index("embeddings_ws_model_project", "workspace_id", "model", "project_id"),
    )
    op.execute(
        f"CREATE INDEX {DEFAULT_INDEX} ON embeddings"
        f" USING hnsw ((embedding::vector({DEFAULT_DIMS})) vector_cosine_ops)"
        f" WITH (m = 16, ef_construction = 64) WHERE model = '{DEFAULT_MODEL}'"
    )


def downgrade() -> None:
    drop_tenant_table("embeddings")
    drop_tenant_table("embedding_models")
