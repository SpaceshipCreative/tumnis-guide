"""planning SQLAlchemy tables owned by this module (mirrors of revisions planning_0001 and
planning_0002)."""

from datetime import date, datetime, time
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, SmallInteger
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
    position: Mapped[int] = mapped_column(SmallInteger)
    reason: Mapped[str]
    block_start: Mapped[datetime | None]
    block_end: Mapped[datetime | None]
    accepted_at: Mapped[datetime | None]
    removed_at: Mapped[datetime | None]
    swapped_from_task_id: Mapped[UUID | None]
