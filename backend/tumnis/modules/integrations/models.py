"""integrations SQLAlchemy tables owned by this module (mirrors of revision
integrations_0001)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, LargeBinary, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tumnis.core.base import Base, TenantBase
from tumnis.core.canonical import CanonicalColumns


class Connection(TenantBase, Base):
    __tablename__ = "connections"

    kind: Mapped[str]
    provider: Mapped[str]
    owner: Mapped[str | None]
    account: Mapped[str]
    status: Mapped[str] = mapped_column(server_default=text("'pending_auth'"))
    cursor: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    credentials_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[int | None]
    last_sync_at: Mapped[datetime | None]
    last_error: Mapped[str | None]


class RawPayload(TenantBase, Base):
    __tablename__ = "raw_payloads"

    connection_id: Mapped[UUID] = mapped_column(ForeignKey("connections.id"))
    record_type: Mapped[str]
    external_id: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fetched_at: Mapped[datetime]


class SyncState(TenantBase, Base):
    __tablename__ = "sync_state"

    connection_id: Mapped[UUID] = mapped_column(ForeignKey("connections.id"))
    cursor: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_page_at: Mapped[datetime | None]
    items_seen: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))


class Person(CanonicalColumns, TenantBase, Base):
    __tablename__ = "people"

    display_name: Mapped[str | None]
    primary_email: Mapped[str] = mapped_column(CITEXT)
    emails: Mapped[list[str]] = mapped_column(ARRAY(CITEXT), server_default=text("'{}'"))
    domains: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))


class Thread(CanonicalColumns, TenantBase, Base):
    __tablename__ = "threads"

    subject: Mapped[str | None]
    participants: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    last_message_at: Mapped[datetime | None]


class Message(CanonicalColumns, TenantBase, Base):
    __tablename__ = "messages"

    thread_id: Mapped[UUID | None] = mapped_column(ForeignKey("threads.id"))
    sent_at: Mapped[datetime | None]
    from_addr: Mapped[str | None]
    to_addrs: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    subject: Mapped[str | None]
    body_text: Mapped[str | None]
    body_html_sanitized: Mapped[str | None]
    labels: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))


class Note(CanonicalColumns, TenantBase, Base):
    __tablename__ = "notes"

    title: Mapped[str | None]
    start_at: Mapped[datetime | None]
    end_at: Mapped[datetime | None]
    attendees: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    body_text: Mapped[str | None]
    action_items: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    event_external_id: Mapped[str | None]


class Artifact(CanonicalColumns, TenantBase, Base):
    __tablename__ = "artifacts"

    kind: Mapped[str]
    url: Mapped[str | None]
    state: Mapped[str | None]
    checks: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    tainted: Mapped[bool] = mapped_column(server_default=text("false"))


class ContextItem(TenantBase, Base):
    __tablename__ = "context_items"

    owner_type: Mapped[str]
    owner_id: Mapped[UUID]
    target_type: Mapped[str]
    target_id: Mapped[UUID | None]
    target_url: Mapped[str | None]
    tainted: Mapped[bool] = mapped_column(server_default=text("true"))
    added_by: Mapped[str]
