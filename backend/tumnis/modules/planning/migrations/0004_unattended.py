"""unattended_windows and unattended_runs (P4-04, FR-4.5, SAF-1, J7).

- `unattended_windows`: when queued AI tasks may run unattended, as weekdays (0 = Monday)
  and two local wall times in the workspace timezone (an end before the start crosses
  midnight). One workspace window (`project_id` NULL) and at most one override per
  project, each a partial unique index over live rows (`workspace_id` leads both).
- `unattended_runs`: each run the unattended tick started, with its window and when its
  result is released to the morning review. Read by agents' result hook (the `result`
  item's `batch` "overnight") and the `unattended_runs_per_week` metric. Planning keeps
  this itself instead of a column on `runs` (agents' table).

No foreign keys to `projects`, `tasks` or `runs` (as `plan_items`): purging another
module's rows must not reach into this module's.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "planning_0004"
down_revision = "planning_0003"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "unattended_windows",
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.Column("weekdays", ARRAY(sa.SmallInteger), nullable=False),
        sa.Column("start_local", sa.Time, nullable=False),
        sa.Column("end_local", sa.Time, nullable=False),
        sa.CheckConstraint(
            "cardinality(weekdays) BETWEEN 1 AND 7"
            " AND weekdays <@ ARRAY[0,1,2,3,4,5,6]::smallint[]",
            name="ck_unattended_windows_weekdays",
        ),
        sa.CheckConstraint("start_local <> end_local", name="ck_unattended_windows_span"),
        sa.Index(
            "uq_unattended_windows_ws_workspace",
            "workspace_id",
            unique=True,
            postgresql_where=sa.text("project_id IS NULL AND deleted_at IS NULL"),
        ),
        sa.Index(
            "uq_unattended_windows_ws_project",
            "workspace_id",
            "project_id",
            unique=True,
            postgresql_where=sa.text("project_id IS NOT NULL AND deleted_at IS NULL"),
        ),
    )
    create_tenant_table(
        "unattended_runs",
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("window_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("window_end", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("release_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Index("uq_unattended_runs_ws_run", "workspace_id", "run_id", unique=True),
        sa.Index("ix_unattended_runs_ws_window", "workspace_id", "window_start"),
    )


def downgrade() -> None:
    drop_tenant_table("unattended_runs")
    drop_tenant_table("unattended_windows")
