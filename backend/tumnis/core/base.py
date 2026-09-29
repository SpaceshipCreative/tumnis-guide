"""Declarative base and the tenant mixin (P0-06, R-01).

Tables are created by Alembic revisions (with create_tenant_table), not from these models;
the models mirror them for queries, and `Base.metadata` is the combined metadata env.py
hands to Alembic.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, MetaData, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_N_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )
    # Timestamps are timestamptz in UTC (AGENTS.md, Time).
    type_annotation_map = {datetime: DateTime(timezone=True)}  # noqa: RUF012


class TenantBase:
    """The base columns create_tenant_table adds; mix into a `Base` model of a tenant table."""

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("uuidv7()"))
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id"), server_default=text("app.current_workspace_id()")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(server_default=text("1"))
    deleted_at: Mapped[datetime | None]
    created_by: Mapped[str] = mapped_column(server_default=text("app.current_actor()"))
