"""planning SQLAlchemy tables owned by this module (mirrors of revision planning_0001)."""

from datetime import time

from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from tumnis.core.base import Base, TenantBase


class WorkingHours(TenantBase, Base):
    __tablename__ = "working_hours"

    weekday: Mapped[int] = mapped_column(Integer)  # 0 = Monday
    start_local: Mapped[time]
    end_local: Mapped[time]
