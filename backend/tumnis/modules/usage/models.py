"""usage SQLAlchemy tables owned by this module (mirrors of revision usage_0001)."""

from datetime import date
from uuid import UUID

from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from tumnis.core.base import Base, TenantBase


class UsageCounter(TenantBase, Base):
    """A workspace's running value of one counter on one UTC day."""

    __tablename__ = "usage_counters"

    day: Mapped[date]
    counter: Mapped[str]
    value: Mapped[int] = mapped_column(BigInteger)


class UsageLedger(TenantBase, Base):
    """What one event added to one counter: the guard against counting it twice."""

    __tablename__ = "usage_ledger"

    event_id: Mapped[UUID]
    counter: Mapped[str]
    day: Mapped[date]
    amount: Mapped[int] = mapped_column(BigInteger)
