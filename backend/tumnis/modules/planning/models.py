"""planning SQLAlchemy tables owned by this module (mirrors of revisions planning_0001 to
planning_0004)."""

from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, SmallInteger
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tumnis.core.base import Base, TenantBase


class WorkingHours(TenantBase, Base):
    __tablename__ = "working_hours"

    weekday: Mapped[int] = mapped_column(Integer)  # 0 = Monday
    start_local: Mapped[time]
    end_local: Mapped[time]


class DailyPlan(TenantBase, Base):
    """Mirror of revision planning_0002."""

    __tablename__ = "daily_plans"

    day: Mapped[date]
    built_at: Mapped[datetime]
    source: Mapped[str]  # master, fallback or manual
    trigger: Mapped[str]  # morning, replan or manual
    status: Mapped[str]  # published or superseded
    notice: Mapped[str | None]
    fallback_reason: Mapped[str | None]
    master_run_id: Mapped[UUID | None]
    profile_version: Mapped[str | None]


class PlanItem(TenantBase, Base):
    __tablename__ = "plan_items"

    plan_id: Mapped[UUID] = mapped_column(ForeignKey("daily_plans.id"))
    task_id: Mapped[UUID]  # no foreign key: see planning_0002
    position: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str]
    block_start: Mapped[datetime | None]
    block_end: Mapped[datetime | None]
    accepted_at: Mapped[datetime | None]
    removed_at: Mapped[datetime | None]
    swapped_from_task_id: Mapped[UUID | None]


class PlanIssue(TenantBase, Base):
    """Mirror of revision planning_0003."""

    __tablename__ = "plan_issues"

    plan_id: Mapped[UUID] = mapped_column(ForeignKey("daily_plans.id"))
    task_id: Mapped[UUID]  # no foreign key: see planning_0003
    kind: Mapped[str]  # no_gap or no_estimate
    offer: Mapped[dict[str, Any]] = mapped_column(JSONB)
    review_item_id: Mapped[UUID | None]
    resolved_at: Mapped[datetime | None]


class PlanPin(TenantBase, Base):
    __tablename__ = "plan_pins"

    task_id: Mapped[UUID]
    day: Mapped[date]


class UnattendedWindow(TenantBase, Base):
    """Mirror of revision planning_0004: the workspace's window (`project_id` NULL) or one
    project's override."""

    __tablename__ = "unattended_windows"

    project_id: Mapped[UUID | None]
    weekdays: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger))
    start_local: Mapped[time]
    end_local: Mapped[time]


class UnattendedRun(TenantBase, Base):
    """Mirror of revision planning_0004: a run the unattended tick started."""

    __tablename__ = "unattended_runs"

    run_id: Mapped[UUID]
    task_id: Mapped[UUID]
    project_id: Mapped[UUID]
    window_start: Mapped[datetime]
    window_end: Mapped[datetime]
    release_at: Mapped[datetime]
