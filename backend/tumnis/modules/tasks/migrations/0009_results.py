"""results: a run's result (P2-04, FR-5.8): what an agent reports it did; one per run.

- `outcome`: done, partial or blocked (`ck_results_outcome`).
- `summary`: the agent's account of the work.
- `files_touched` and `links`: JSON lists, empty by default.
- `tests_summary`: how the tests went, when the agent ran any.
- `tainted`: posted by a tainted run or by a key with no run (P2-08, SAF-1).

`task_id` is the task the run worked on (ON DELETE CASCADE: a purged task takes its
results with it, as its comments and undo log go); `run_id` is the agents module's `runs`
row, with no foreign key (runs belong to agents). `ux_results_ws_run` keeps one result per run, so a
replayed report lands once; `ix_results_ws_task` lists a task's results. `workspace_id`
leads both indexes.

Chained after P1-08's enrichment columns (tasks_0008).
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "tasks_0009"
down_revision = "tasks_0008"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "results",
        sa.Column(
            "task_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("files_touched", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("links", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("tests_summary", sa.Text, nullable=True),
        sa.Column("tainted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.CheckConstraint("outcome IN ('done', 'partial', 'blocked')", name="ck_results_outcome"),
        sa.Index("ux_results_ws_run", "workspace_id", "run_id", unique=True),
        sa.Index("ix_results_ws_task", "workspace_id", "task_id"),
    )


def downgrade() -> None:
    drop_tenant_table("results")
