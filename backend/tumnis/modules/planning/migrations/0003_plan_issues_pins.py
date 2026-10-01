"""plan_issues and plan_pins (P1-11, J6).

- `plan_issues`: a pick the planner could not place (no big enough gap), with the offer
  made for it (`offer`: `{split, move_to}`), the `plan_issue` review item that carries it
  and when it was resolved (split taken, move taken, or kept off today).
- `plan_pins`: a task moved to a later day; it heads that day's planning candidates. One
  pin per task and day.

No foreign key to `tasks` (as `plan_items`): purging a trashed task must not reach into
this module's rows.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "planning_0003"
down_revision = "planning_0002"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "plan_issues",
        sa.Column("plan_id", UUID(as_uuid=True), sa.ForeignKey("daily_plans.id"), nullable=False),
        sa.Column("task_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("offer", JSONB, nullable=False),
        sa.Column("review_item_id", UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('no_gap', 'no_estimate')", name="ck_plan_issues_kind"),
        sa.Index("uq_plan_issues_ws_plan_task", "workspace_id", "plan_id", "task_id", unique=True),
    )
    create_tenant_table(
        "plan_pins",
        sa.Column("task_id", UUID(as_uuid=True), nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Index("uq_plan_pins_ws_task_day", "workspace_id", "task_id", "day", unique=True),
    )


def downgrade() -> None:
    drop_tenant_table("plan_pins")
    drop_tenant_table("plan_issues")
