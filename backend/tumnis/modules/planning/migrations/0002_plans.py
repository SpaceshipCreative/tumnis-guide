"""daily_plans and plan_items (P1-12 creates them for the week view; P1-11 fills them).

- `daily_plans`: one plan per local day and build, with where it came from (`source`:
  master, fallback or manual), what built it (`trigger`) and whether it is the day's
  current plan (`status`: published or superseded; at most one published per day).
  P1-11's columns (notice, fallback_reason, master_run_id, profile_version) are here too,
  so its planner writes the same rows.
- `plan_items`: the tasks of a plan in order, each with its reason and, for Human and
  Hybrid tasks, its block. A block scheduled from the project Calendar view is an item of
  the day's published plan (a `manual` plan when the day had none): the Today panel and
  focus events read the same record. No foreign key to `tasks`: purging a trashed task
  must not reach into this module's rows.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "planning_0002"
down_revision = "planning_0001"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "daily_plans",
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("built_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("trigger", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("notice", sa.Text, nullable=True),
        sa.Column("fallback_reason", sa.Text, nullable=True),
        sa.Column("master_run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("profile_version", sa.Text, nullable=True),
        sa.CheckConstraint(
            "source IN ('master', 'fallback', 'manual')", name="ck_daily_plans_source"
        ),
        sa.CheckConstraint(
            "trigger IN ('morning', 'replan', 'manual')", name="ck_daily_plans_trigger"
        ),
        sa.CheckConstraint("status IN ('published', 'superseded')", name="ck_daily_plans_status"),
        sa.CheckConstraint(
            "notice IN ('agent_offline', 'invalid_plan')", name="ck_daily_plans_notice"
        ),
        sa.Index(
            "ux_daily_plans_one_published",
            "workspace_id",
            "day",
            unique=True,
            postgresql_where=sa.text("status = 'published'"),
        ),
    )
    create_tenant_table(
        "plan_items",
        sa.Column("plan_id", UUID(as_uuid=True), sa.ForeignKey("daily_plans.id"), nullable=False),
        sa.Column("task_id", UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.SmallInteger, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("block_start", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("block_end", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("removed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("swapped_from_task_id", UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "(block_start IS NULL) = (block_end IS NULL)", name="ck_plan_items_block_pair"
        ),
        sa.CheckConstraint(
            "block_end IS NULL OR block_end > block_start", name="ck_plan_items_block_order"
        ),
        sa.Index("uq_plan_items_ws_plan_task", "workspace_id", "plan_id", "task_id", unique=True),
    )


def downgrade() -> None:
    drop_tenant_table("plan_items")
    drop_tenant_table("daily_plans")
