"""calendar SQLAlchemy tables owned by this module (mirrors of revisions calendar_0001 and
calendar_0002)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tumnis.core.base import Base, TenantBase
from tumnis.core.canonical import CanonicalColumns


class Event(CanonicalColumns, TenantBase, Base):
    __tablename__ = "events"

    title: Mapped[str | None]
    start_at: Mapped[datetime]
    end_at: Mapped[datetime]
    all_day: Mapped[bool] = mapped_column(server_default=text("false"))
    attendees: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    busy: Mapped[bool] = mapped_column(server_default=text("true"))
    tainted: Mapped[bool] = mapped_column(server_default=text("false"))
    calendar_id: Mapped[str | None]


class CalendarAccount(TenantBase, Base):
    __tablename__ = "calendar_accounts"

    connection_id: Mapped[UUID] = mapped_column(ForeignKey("connections.id"))
    google_email: Mapped[str]
    calendars: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    selected_calendar_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'")
    )
    status: Mapped[str] = mapped_column(server_default=text("'connected'"))
    last_sync_at: Mapped[datetime | None]
