"""coolify SQLAlchemy tables owned by this module (mirrors of revision coolify_0001)."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tumnis.core.base import Base, TenantBase


class DeploymentStatus(TenantBase, Base):
    """The last poll of one linked Coolify application (FR-12.2)."""

    __tablename__ = "deployment_status"

    app_uuid: Mapped[str]
    app_name: Mapped[str | None]
    status: Mapped[str | None]
    deployment_uuid: Mapped[str | None]
    commit: Mapped[str | None]
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    previews: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None]
