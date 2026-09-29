"""tasks SQLAlchemy tables owned by this module (mirrors of revisions tasks_0001 and
tasks_0003).

`board_rank` and `sort_key` compare bytewise (`COLLATE "C"`), so Postgres orders the
fractional keys as Python and TypeScript do (core/rank.py)."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tumnis.core.base import Base, TenantBase

STATUS_ENUM = ENUM(
    "backlog",
    "today",
    "in_progress",
    "waiting_on_human",
    "in_review",
    "done",
    name="task_status",
    create_type=False,
)
PRIORITY_ENUM = ENUM("low", "normal", "high", "urgent", name="task_priority", create_type=False)
LABEL_ENUM = ENUM("human", "ai", "hybrid", name="task_label", create_type=False)


class BoardColumn(TenantBase, Base):
    __tablename__ = "board_columns"

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str]
    status_map: Mapped[str] = mapped_column(STATUS_ENUM)
    sort_key: Mapped[str] = mapped_column(Text(collation="C"))


class Task(TenantBase, Base):
    __tablename__ = "tasks"

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id"))
    title: Mapped[str]
    label: Mapped[str | None] = mapped_column(LABEL_ENUM)
    label_source: Mapped[str | None]
    status: Mapped[str] = mapped_column(STATUS_ENUM, server_default=text("'backlog'"))
    priority: Mapped[str] = mapped_column(PRIORITY_ENUM, server_default=text("'normal'"))
    due_on: Mapped[date | None]
    estimate_minutes: Mapped[int | None]
    first_action: Mapped[str | None]
    acceptance_criteria: Mapped[str | None]
    assigned_agent_id: Mapped[UUID | None]
    board_rank: Mapped[str] = mapped_column(Text(collation="C"))
    column_id: Mapped[UUID | None] = mapped_column(ForeignKey("board_columns.id"))
    rollover_count: Mapped[int] = mapped_column(server_default=text("0"))
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    actual_minutes: Mapped[int | None]
    tainted: Mapped[bool] = mapped_column(server_default=text("false"))
    source: Mapped[str] = mapped_column(server_default=text("'user'"))


class TaskComment(TenantBase, Base):
    __tablename__ = "task_comments"

    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"))
    body_md: Mapped[str]


class TaskContextItem(TenantBase, Base):
    __tablename__ = "task_context_items"

    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"))
    context_item_id: Mapped[UUID] = mapped_column(ForeignKey("context_items.id"))


class TaskChange(TenantBase, Base):
    """One task write's undoable fields before and after (P0-24, R-09)."""

    __tablename__ = "task_changes"

    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"))
    change_id: Mapped[UUID]
    actor: Mapped[str]
    before: Mapped[dict[str, Any]] = mapped_column(JSONB)
    after: Mapped[dict[str, Any]] = mapped_column(JSONB)
    undone_at: Mapped[datetime | None]


class ReviewItem(TenantBase, Base):
    __tablename__ = "review_items"

    kind: Mapped[str]
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("projects.id"))
    target_type: Mapped[str]
    target_id: Mapped[UUID]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    dedupe_key: Mapped[str | None]
    blocking_impact: Mapped[str | None]
    snoozed_until: Mapped[datetime | None]
    decided_at: Mapped[datetime | None]
    decision: Mapped[str | None]
