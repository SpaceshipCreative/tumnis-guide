"""decisions SQLAlchemy tables owned by this module (mirrors of revision decisions_0001)."""

from sqlalchemy import LargeBinary
from sqlalchemy.orm import Mapped, mapped_column

from tumnis.core.base import Base, TenantBase


class ProviderConfig(TenantBase, Base):
    """One AI slot's provider, fallback, pinned model and sealed credential (FR-11.1)."""

    __tablename__ = "provider_configs"

    slot: Mapped[str]
    primary: Mapped[str]
    fallback: Mapped[str | None]
    credentials_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    model_version: Mapped[str]
