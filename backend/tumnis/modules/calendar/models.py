"""calendar SQLAlchemy tables owned by this module (mirrors of revision calendar_0001)."""

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
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
