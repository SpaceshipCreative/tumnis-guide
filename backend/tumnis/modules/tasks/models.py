"""tasks SQLAlchemy tables owned by this module (mirrors of revisions tasks_0001,
tasks_0003, tasks_0004, tasks_0005, tasks_0006, tasks_0007 and tasks_0008, P2-04's results).

`board_rank` and `sort_key` compare bytewise (`COLLATE "C"`), so Postgres orders the
fractional keys as Python and TypeScript do (core/rank.py)."""

from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB
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
    # P1-07: the label's one-line reason and the decision behind it (no foreign key, as
    # below); a low-confidence answer waits in `label_suggestion` while `label` stays NULL.
    label_reason: Mapped[str | None]
    label_confidence: Mapped[float | None]
    label_decision_id: Mapped[UUID | None]
    label_suggestion: Mapped[str | None] = mapped_column(LABEL_ENUM)
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
    # No foreign key: T-P0-18-17 lets the tasks tables reference only workspaces, projects,
    # tasks, board columns and context items. Rules are only soft-deleted.
    recurrence_rule_id: Mapped[UUID | None]
    occurrence_on: Mapped[date | None]  # the local date this instance of its rule stands for


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
    task_version: Mapped[int]  # the task's version this write left; an undo must name it
    undone_at: Mapped[datetime | None]


class ReviewItem(TenantBase, Base):
    __tablename__ = "review_items"

    kind: Mapped[str]
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("projects.id"))
    target_type: Mapped[str]
    target_id: Mapped[UUID]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    dedupe_key: Mapped[str | None]
    blocking_impact: Mapped[str | None]  # a float's text (P0-18); the queue casts it (P1-13)
    jev_factor: Mapped[float | None]  # P1-13, revision tasks_0005
    decision_id: Mapped[UUID | None]  # P1-13: the blocking-impact decision that set it
    snoozed_until: Mapped[datetime | None]
    decided_at: Mapped[datetime | None]
    decision: Mapped[str | None]
    flags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))


class Result(TenantBase, Base):
    """A run's result: what an agent reports it did, one per run (P2-04, revision
    tasks_0008). `run_id` is agents' `runs` row: no cross-module foreign key."""

    __tablename__ = "results"

    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    run_id: Mapped[UUID]
    outcome: Mapped[str]  # done, partial or blocked (ck_results_outcome)
    summary: Mapped[str]
    files_touched: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    links: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    tests_summary: Mapped[str | None]


class RecurrenceRule(TenantBase, Base):
    """A recurring task's rule (P0-19, revision tasks_0002)."""

    __tablename__ = "recurrence_rules"

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    task_template: Mapped[dict[str, Any]] = mapped_column(JSONB)
    preset: Mapped[str | None]
    cron: Mapped[str | None]
    weekday: Mapped[int | None]
    month_day: Mapped[int | None]
    due_time: Mapped[time] = mapped_column(server_default=text("'09:00'"))
    latest_occurrence_at: Mapped[datetime | None]
    next_due_at: Mapped[datetime | None]


class DayClose(TenantBase, Base):
    """One closed local day of a workspace (P0-19, revision tasks_0002)."""

    __tablename__ = "day_closes"

    day: Mapped[date]
    closed_at: Mapped[datetime]
    rolled_over: Mapped[int] = mapped_column(server_default=text("0"))
