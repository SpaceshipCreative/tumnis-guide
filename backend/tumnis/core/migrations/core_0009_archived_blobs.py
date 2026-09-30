"""archived_blobs: what an archived project keeps compressed in Postgres (P2-18, FR-5.10).

A tenant table (the base columns, RLS). Each row is one zstd-compressed blob that a module
wrote while archiving a project: its run logs, its context items, a folder's index rows,
or the facts of its profile archive on the agent server. `(module, kind, project_id, ref)`
names a blob once per workspace, so a replayed archive step writes nothing new
(`ON CONFLICT DO NOTHING`). No foreign key to `projects`: core sits below the modules.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "core_0009_archived_blobs"
down_revision = "core_0008_fake_scripts"
branch_labels = None
depends_on = "auth_0001"  # archived_blobs.workspace_id -> workspaces
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "archived_blobs",
        sa.Column("module", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("ref", sa.Text, nullable=False),
        sa.Column("codec", sa.Text, nullable=False, server_default=sa.text("'zstd'")),
        sa.Column("raw_size", sa.BigInteger, nullable=False),
        sa.Column("stored_size", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.Text, nullable=False),
        sa.Column("data", sa.LargeBinary, nullable=False),
        sa.CheckConstraint("codec IN ('zstd')", name="ck_archived_blobs_codec"),
        sa.Index(
            "uq_archived_blobs_ref",
            "workspace_id",
            "module",
            "kind",
            "project_id",
            "ref",
            unique=True,
        ),
        sa.Index("ix_archived_blobs_project", "workspace_id", "project_id"),
    )


def downgrade() -> None:
    drop_tenant_table("archived_blobs")
