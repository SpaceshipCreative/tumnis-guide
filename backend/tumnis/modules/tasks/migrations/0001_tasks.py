"""tasks, board_columns, task_comments, task_context_items and review_items (P0-18,
FR-3.1, FR-3.2, FR-3.4, FR-14.2, R-03, R-08).

- board_columns: a project's kanban columns in board order (`sort_key`, a core/rank.py
  key compared bytewise); each holds one status (`status_map`), several may share one.
- tasks: every FR-3.1 field. `status` and `priority` are Postgres enums; `label` is a
  nullable enum (NULL: pending, R-08) and `label_source` says who set it (NULL exactly while
  the label is pending). AI-only work carries no estimate. `board_rank` orders a task inside
  its column (`COLLATE "C"`). A subtask's `parent_id` is a task of the same project (the api
  checks it; depth one).
- task_comments: markdown comments on a task.
- task_context_items: the task's links to outside content, by ContextItem id only (FR-14.2).
- review_items: human decisions queued by any module (R-03). `kind` is plain text checked by
  the kind registry, not an enum; an open item's `dedupe_key` is unique.

All are tenant tables; `workspace_id` leads every multi-column index.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "tasks_0001"
down_revision = None
branch_labels = ("tasks",)
depends_on = ("auth_0001", "projects_0001", "integrations_0001")
phase = "expand"

STATUSES = ("backlog", "today", "in_progress", "waiting_on_human", "in_review", "done")
status_enum = ENUM(*STATUSES, name="task_status", create_type=False)
priority_enum = ENUM("low", "normal", "high", "urgent", name="task_priority", create_type=False)
label_enum = ENUM("human", "ai", "hybrid", name="task_label", create_type=False)
ENUMS = (status_enum, priority_enum, label_enum)


def _uuid_fk(name: str, target: str, *, nullable: bool = False) -> sa.Column[object]:
    return sa.Column(name, UUID(as_uuid=True), sa.ForeignKey(target), nullable=nullable)


def upgrade() -> None:
    for enum in ENUMS:
        enum.create(op.get_bind(), checkfirst=False)

    create_tenant_table(
        "board_columns",
        _uuid_fk("project_id", "projects.id"),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("status_map", status_enum, nullable=False),
        sa.Column("sort_key", sa.Text(collation="C"), nullable=False),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 60", name="ck_board_columns_name"),
        sa.Index(
            "ix_board_columns_ws_project_sort",
            "workspace_id",
            "project_id",
            "sort_key",
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )
    create_tenant_table(
        "tasks",
        _uuid_fk("project_id", "projects.id"),
        _uuid_fk("parent_id", "tasks.id", nullable=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("label", label_enum, nullable=True),
        sa.Column("label_source", sa.Text, nullable=True),
        sa.Column("status", status_enum, nullable=False, server_default="backlog"),
        sa.Column("priority", priority_enum, nullable=False, server_default="normal"),
        sa.Column("due_on", sa.Date, nullable=True),
        sa.Column("estimate_minutes", sa.Integer, nullable=True),
        sa.Column("first_action", sa.Text, nullable=True),
        sa.Column("acceptance_criteria", sa.Text, nullable=True),
        sa.Column("assigned_agent_id", UUID(as_uuid=True), nullable=True),
        sa.Column("board_rank", sa.Text(collation="C"), nullable=False),
        _uuid_fk("column_id", "board_columns.id", nullable=True),
        sa.Column("rollover_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("actual_minutes", sa.Integer, nullable=True),
        sa.Column("tainted", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("source", sa.Text, nullable=False, server_default=sa.text("'user'")),
        sa.CheckConstraint(
            "label_source IN ('user', 'jev', 'agent', 'fallback')", name="ck_tasks_label_source"
        ),
        sa.CheckConstraint(
            "(label IS NULL) = (label_source IS NULL)", name="ck_tasks_label_source_set"
        ),
        sa.CheckConstraint(
            "estimate_minutes IS NULL OR label IS DISTINCT FROM 'ai'", name="ck_tasks_ai_estimate"
        ),
        sa.CheckConstraint(
            "estimate_minutes IS NULL OR estimate_minutes BETWEEN 1 AND 960",
            name="ck_tasks_estimate_range",
        ),
        sa.CheckConstraint("rollover_count >= 0", name="ck_tasks_rollover_count"),
        sa.CheckConstraint(
            "actual_minutes IS NULL OR actual_minutes >= 0", name="ck_tasks_actual_minutes"
        ),
        sa.CheckConstraint("parent_id IS DISTINCT FROM id", name="ck_tasks_not_own_parent"),
        sa.Index(
            "ix_tasks_ws_project_status",
            "workspace_id",
            "project_id",
            "status",
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
        sa.Index(
            "ix_tasks_ws_parent",
            "workspace_id",
            "parent_id",
            postgresql_where=sa.text("parent_id IS NOT NULL"),
        ),
        sa.Index(
            "ix_tasks_ws_column_rank",
            "workspace_id",
            "column_id",
            "board_rank",
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )
    create_tenant_table(
        "task_comments",
        _uuid_fk("task_id", "tasks.id"),
        sa.Column("body_md", sa.Text, nullable=False),
        sa.Index("ix_task_comments_ws_task", "workspace_id", "task_id"),
    )
    create_tenant_table(
        "task_context_items",
        _uuid_fk("task_id", "tasks.id"),
        _uuid_fk("context_item_id", "context_items.id"),
        sa.Index(
            "ux_task_context_items_ws_task_item",
            "workspace_id",
            "task_id",
            "context_item_id",
            unique=True,
        ),
    )
    create_tenant_table(
        "review_items",
        sa.Column("kind", sa.Text, nullable=False),
        _uuid_fk("project_id", "projects.id", nullable=True),
        sa.Column("target_type", sa.Text, nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("dedupe_key", sa.Text, nullable=True),
        sa.Column("blocking_impact", sa.Text, nullable=True),
        sa.Column("snoozed_until", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("decision", sa.Text, nullable=True),
        sa.CheckConstraint("kind ~ '^[a-z][a-z0-9_]{2,40}$'", name="ck_review_items_kind"),
        sa.Index(
            "ux_review_items_ws_dedupe_open",
            "workspace_id",
            "dedupe_key",
            unique=True,
            postgresql_where=sa.text("decided_at IS NULL AND deleted_at IS NULL"),
        ),
        sa.Index(
            "ix_review_items_ws_open",
            "workspace_id",
            "snoozed_until",
            postgresql_where=sa.text("decided_at IS NULL AND deleted_at IS NULL"),
        ),
    )


def downgrade() -> None:
    for table in ("review_items", "task_context_items", "task_comments", "tasks", "board_columns"):
        drop_tenant_table(table)
    for enum in reversed(ENUMS):
        enum.drop(op.get_bind(), checkfirst=False)
