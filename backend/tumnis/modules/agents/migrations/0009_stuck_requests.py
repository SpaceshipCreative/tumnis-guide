"""Stuck requests (P4-02, FR-10.5): what the focus bar shows after "Stuck".

- `stuck_requests`: one row per stuck focus event (`focus_event_id`, focus's row, so no
  cross-module foreign key) on a task (`task_id`, tasks' row): the stuck run it asked for
  (`run_id`, null when no run could start), and where it stands: `working` until the agent
  answers, `split` (it posted a first step, `step_task_id`), `took_step` (it took the step
  itself and reported back, `summary`) or `fallback` (no answer within the deadline, or the
  agent was down: the task's first action with a 10-minute timer). A late step replaces
  the fallback. `requested_at` is the stuck event's time; `resolved_at` and `fallback_at`
  when each happened.
- `ux_stuck_requests_ws_event`: one request per focus event, so a redelivered event or a
  replayed workflow step adds nothing.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "agents_0009"
down_revision = "agents_0008"
branch_labels = None
depends_on = None
phase = "expand"

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    create_tenant_table(
        "stuck_requests",
        sa.Column("focus_event_id", UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'working'")),
        sa.Column("step_task_id", UUID(as_uuid=True), nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("requested_at", TS, nullable=False),
        sa.Column("resolved_at", TS, nullable=True),
        sa.Column("fallback_at", TS, nullable=True),
        sa.CheckConstraint(
            "state IN ('working', 'split', 'took_step', 'fallback')",
            name="ck_stuck_requests_state",
        ),
        sa.Index("ux_stuck_requests_ws_event", "workspace_id", "focus_event_id", unique=True),
        sa.Index("ix_stuck_requests_ws_run", "workspace_id", "run_id"),
        sa.Index("ix_stuck_requests_ws_requested", "workspace_id", "requested_at"),
    )


def downgrade() -> None:
    drop_tenant_table("stuck_requests")
