"""recurrence_rules, day_closes, the recurrence columns on tasks and cascading task children
(P0-19, FR-3.5, FR-3.6, REL-6).

- recurrence_rules: a recurring task's rule (a preset or a 5-field cron, never both, with
  the preset's weekday or month day and the local due time), the template successors copy
  (`task_template`), the latest instance's occurrence (`latest_occurrence_at`) and the
  next one after it (`next_due_at`, for the Schedule rail).
- tasks.recurrence_rule_id / occurrence_on: an instance of a rule and the local date it
  stands for; `ux_tasks_ws_rule_occurrence` allows one instance per rule and date, so
  completion and the recurrence tick racing make one successor. The foreign key is added
  NOT VALID (squawk's constraint-missing-not-valid): existing rows carry no rule.
- day_closes: one row per workspace and closed local day (`ux_day_closes_ws_day`), with
  when it closed and how many Today tasks rolled over; the last `closed_at` anchors the
  next close.
- task_comments and task_context_items now go with their task when the trash purge
  hard-deletes it (ON DELETE CASCADE, re-added NOT VALID).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "tasks_0002"
down_revision = "tasks_0001"
branch_labels = None
depends_on = None
phase = "expand"

CHILDREN = ("task_comments", "task_context_items")


def _fk(table: str, *, cascade: bool) -> None:
    name = f"fk_{table}_task_id_tasks"
    op.drop_constraint(name, table, type_="foreignkey")
    on_delete = " ON DELETE CASCADE" if cascade else ""
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {name}"
        f" FOREIGN KEY (task_id) REFERENCES tasks (id){on_delete} NOT VALID"
    )


def upgrade() -> None:
    create_tenant_table(
        "recurrence_rules",
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("task_template", JSONB, nullable=False),
        sa.Column("preset", sa.Text, nullable=True),
        sa.Column("cron", sa.Text, nullable=True),
        sa.Column("weekday", sa.SmallInteger, nullable=True),
        sa.Column("month_day", sa.SmallInteger, nullable=True),
        sa.Column("due_time", sa.Time, nullable=False, server_default=sa.text("'09:00'")),
        sa.Column("latest_occurrence_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("next_due_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "preset IN ('daily', 'weekdays', 'weekly', 'monthly')",
            name="ck_recurrence_rules_preset",
        ),
        sa.CheckConstraint(
            "(preset IS NULL) <> (cron IS NULL)", name="ck_recurrence_rules_preset_or_cron"
        ),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_recurrence_rules_weekday"),
        sa.CheckConstraint("month_day BETWEEN 1 AND 31", name="ck_recurrence_rules_month_day"),
        sa.CheckConstraint("length(cron) <= 120", name="ck_recurrence_rules_cron"),
        sa.Index(
            "ix_recurrence_rules_ws_project",
            "workspace_id",
            "project_id",
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )
    op.add_column("tasks", sa.Column("recurrence_rule_id", UUID(as_uuid=True), nullable=True))
    op.add_column("tasks", sa.Column("occurrence_on", sa.Date, nullable=True))
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT fk_tasks_recurrence_rule_id_recurrence_rules"
        " FOREIGN KEY (recurrence_rule_id) REFERENCES recurrence_rules (id) NOT VALID"
    )
    op.create_index(
        "ux_tasks_ws_rule_occurrence",
        "tasks",
        ["workspace_id", "recurrence_rule_id", "occurrence_on"],
        unique=True,
        postgresql_where=sa.text("recurrence_rule_id IS NOT NULL"),
    )
    create_tenant_table(
        "day_closes",
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("rolled_over", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint("rolled_over >= 0", name="ck_day_closes_rolled_over"),
        sa.Index("ux_day_closes_ws_day", "workspace_id", "day", unique=True),
        sa.Index("ix_day_closes_ws_closed_at", "workspace_id", "closed_at"),
    )
    for table in CHILDREN:
        _fk(table, cascade=True)


def downgrade() -> None:
    for table in CHILDREN:
        _fk(table, cascade=False)
    drop_tenant_table("day_closes")
    op.drop_index("ux_tasks_ws_rule_occurrence", table_name="tasks")
    op.drop_constraint("fk_tasks_recurrence_rule_id_recurrence_rules", "tasks", type_="foreignkey")
    op.drop_column("tasks", "occurrence_on")
    op.drop_column("tasks", "recurrence_rule_id")
    drop_tenant_table("recurrence_rules")
