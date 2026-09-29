"""auth SQLAlchemy tables owned by this module."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Mapped, mapped_column

from tumnis.core.base import Base


class Workspace(Base):
    """The tenant root (P0-06). Not a TenantBase table: its own id is the tenant."""

    __tablename__ = "workspaces"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("uuidv7()"))
    name: Mapped[str]
    timezone: Mapped[str] = mapped_column(server_default="UTC")
    deployment_mode: Mapped[str] = mapped_column(server_default="self-hosted")
    subtask_threshold_min: Mapped[int] = mapped_column(server_default=text("30"))  # FR-3.8
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(server_default=text("1"))
    deleted_at: Mapped[datetime | None]
    created_by: Mapped[str] = mapped_column(server_default=text("app.current_actor()"))
