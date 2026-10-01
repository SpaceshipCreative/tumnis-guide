"""focus SQLAlchemy tables owned by this module (mirrors of revision focus_0001)."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from tumnis.core.base import Base, TenantBase


class FocusSession(TenantBase, Base):
    __tablename__ = "focus_sessions"

    task_id: Mapped[UUID]  # no foreign key: the tasks module's row
    project_id: Mapped[UUID | None]
    level: Mapped[str]  # the level the session started at
    cadence_min: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]
    streak: Mapped[int] = mapped_column(Integer)
    doubled: Mapped[bool]
    last_check_at: Mapped[datetime]
    snoozed_until: Mapped[datetime | None]
    last_activity_at: Mapped[datetime | None]
    workflow_id: Mapped[str]


class FocusEvent(TenantBase, Base):
    __tablename__ = "focus_events"

    session_id: Mapped[UUID | None] = mapped_column(ForeignKey("focus_sessions.id"))
    task_id: Mapped[UUID | None]
    plan_id: Mapped[UUID | None]
    kind: Mapped[str]
    fired_at: Mapped[datetime]
    level: Mapped[str]
    rule: Mapped[str]
    message: Mapped[str]
    dedupe_key: Mapped[str]


class FocusResponse(TenantBase, Base):
    __tablename__ = "focus_responses"

    event_id: Mapped[UUID] = mapped_column(ForeignKey("focus_events.id"))
    task_id: Mapped[UUID | None]
    response: Mapped[str]
    responded_at: Mapped[datetime]
    to_task_id: Mapped[UUID | None]


class FocusOverride(TenantBase, Base):
    __tablename__ = "focus_overrides"

    day: Mapped[date]
    level: Mapped[str]
