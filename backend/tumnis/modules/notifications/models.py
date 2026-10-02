"""notifications SQLAlchemy tables owned by this module (mirrors of notifications_0001 and
notifications_0002)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tumnis.core.base import Base, TenantBase


class Notification(TenantBase, Base):
    __tablename__ = "notifications"

    kind: Mapped[str]
    target_type: Mapped[str | None]
    target_id: Mapped[UUID | None]  # no foreign key: another module's row
    project_id: Mapped[UUID | None]
    level: Mapped[str]
    decision: Mapped[str]  # "now" or "batch" (rules.delivery_decision)
    released_at: Mapped[datetime | None]  # a batched row: when its batch went
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    dedupe_key: Mapped[str]
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # the notify packet's facts


class PushSubscription(TenantBase, Base):
    __tablename__ = "push_subscriptions"

    user_id: Mapped[UUID]
    endpoint: Mapped[str]
    p256dh: Mapped[str]
    auth: Mapped[str]
    user_agent: Mapped[str | None]
    last_success_at: Mapped[datetime | None]
    failures: Mapped[int] = mapped_column(Integer)


class DeliveryAttempt(TenantBase, Base):
    __tablename__ = "delivery_attempts"

    notification_id: Mapped[UUID] = mapped_column(ForeignKey("notifications.id"))
    channel: Mapped[str]
    subscription_id: Mapped[UUID | None]
    status: Mapped[str]
    status_code: Mapped[int | None]
    attempted_at: Mapped[datetime]
    run_id: Mapped[UUID | None]  # a Discord attempt's notify run (no foreign key: agents')
    error: Mapped[str | None]
