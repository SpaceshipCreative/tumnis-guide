"""An index for a project's audit rows (P2-17, FR-2.6): the project's Activity reads the
rows written with `audit.record(..., project_id=)`, newest first, a page at a time
(`audit.list_for_project`). Only rows that name a project are indexed (a partial index on
`details ? 'project_id'`); the queries repeat that predicate so the planner can use it.
The immutability trigger refuses UPDATE, DELETE and TRUNCATE, not an index.
"""

from alembic import op

revision = "core_0010_audit_project_index"
down_revision = "core_0009_archived_blobs"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_audit_ws_project_occurred ON audit_log"
        " (workspace_id, (details->>'project_id'), occurred_at DESC, id DESC)"
        " WHERE details ? 'project_id'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_audit_ws_project_occurred")
