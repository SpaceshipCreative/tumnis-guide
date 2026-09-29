"""knowledge SQLAlchemy tables owned by this module (mirrors of revision knowledge_0001)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from tumnis.core.base import Base, TenantBase
from tumnis.core.canonical import CanonicalColumns


class Document(CanonicalColumns, TenantBase, Base):
    """Canonical columns, with the key columns nullable: text entries and uploads have no
    connection or external id."""

    __tablename__ = "documents"

    project_id: Mapped[UUID | None]
    title: Mapped[str]
    kind: Mapped[str]
    trust: Mapped[str] = mapped_column(server_default=text("'untrusted'"))
    storage_location_id: Mapped[UUID | None]
    path: Mapped[str | None]
    source_revision: Mapped[str | None]
    pinned: Mapped[bool] = mapped_column(server_default=text("false"))
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    connection_id: Mapped[UUID | None] = mapped_column(ForeignKey("connections.id"))  # type: ignore[assignment]
    external_id: Mapped[str | None]  # type: ignore[assignment]
    fetched_at: Mapped[datetime | None]  # type: ignore[assignment]
