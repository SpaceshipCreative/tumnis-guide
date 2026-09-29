"""auth SQLAlchemy tables owned by this module."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, LargeBinary, text
from sqlalchemy.dialects.postgresql import CITEXT, INET
from sqlalchemy.orm import Mapped, mapped_column

from tumnis.core.base import Base, TenantBase


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


class User(Base):
    """Global identity (P0-13, revision auth_0003): one person across workspaces; the app
    role sees only its own row (`self_only` on app.user_id). Not a tenant table."""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("uuidv7()"))
    email: Mapped[str] = mapped_column(CITEXT)
    password_hash: Mapped[str]
    totp_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    totp_key_version: Mapped[int | None]
    totp_last_step: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    totp_confirmed_at: Mapped[datetime | None]
    home_workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(server_default=text("1"))
    deleted_at: Mapped[datetime | None]


class Membership(TenantBase, Base):
    """A user's role in a workspace (only `owner` in v1)."""

    __tablename__ = "memberships"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str]


class AuthSession(TenantBase, Base):
    """One signed-in device: the database holds HMAC-SHA256(pepper, token), never the
    token (R-21)."""

    __tablename__ = "sessions"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    token_hmac: Mapped[bytes] = mapped_column(LargeBinary)
    device_label: Mapped[str | None]
    user_agent: Mapped[str | None]
    source_ip: Mapped[str | None] = mapped_column(INET)
    second_factor: Mapped[str | None]
    last_seen_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
