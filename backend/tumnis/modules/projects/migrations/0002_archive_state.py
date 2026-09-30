"""project_archives: where a project's archive stands (P2-18, FR-5.10).

One row per project whose data an archive moved or is moving; no row for a live project
(and for one archived before P2-18, whose data was never moved). `state` is `archiving`
while `archive_project` moves its data out, `archived` once it has, and `unarchiving`
while `unarchive_project` brings it back; the row goes when the project is live again.
`projects.archived_at` keeps its meaning (hidden from the lists), set and cleared by the
routes at once.

A table of its own, not a `projects` column: every update of a `projects` row bumps its
version (`app.touch_row`), and the workflows' moves are not edits, so a client holding
the version the archive route answered can still unarchive (T-P0-20-13).
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "projects_0002"
down_revision = "projects_0001"
branch_labels = None
depends_on = "core_0009_archived_blobs"  # the archive keeps its data in archived_blobs
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "project_archives",
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.CheckConstraint(
            "state IN ('archiving', 'archived', 'unarchiving')", name="ck_project_archives_state"
        ),
        sa.Index("ux_project_archives_ws_project", "workspace_id", "project_id", unique=True),
    )


def downgrade() -> None:
    drop_tenant_table("project_archives")
