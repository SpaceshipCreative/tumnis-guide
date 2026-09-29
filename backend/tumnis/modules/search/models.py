"""search SQLAlchemy tables owned by this module (mirror of revision search_0001)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Computed
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from tumnis.core.base import Base, TenantBase

_TSV = (
    "setweight(to_tsvector('english'::regconfig, title), 'A') || "
    "setweight(to_tsvector('simple'::regconfig, title), 'B') || "
    "setweight(to_tsvector('english'::regconfig, body), 'C') || "
    "setweight(to_tsvector('simple'::regconfig, body), 'D')"
)


class SearchIndex(TenantBase, Base):
    """One searchable task or project, written only by the search subscribers."""

    __tablename__ = "search_index"

    entity_type: Mapped[str]
    entity_id: Mapped[UUID]
    project_id: Mapped[UUID | None]
    title: Mapped[str]
    body: Mapped[str]
    source_updated_at: Mapped[datetime]
    tsv: Mapped[str] = mapped_column(TSVECTOR, Computed(_TSV, persisted=True))
