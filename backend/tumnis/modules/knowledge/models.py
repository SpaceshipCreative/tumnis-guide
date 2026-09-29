"""knowledge SQLAlchemy tables owned by this module (mirrors of revisions knowledge_0001
to knowledge_0003)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, LargeBinary, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tumnis.core.base import Base, TenantBase
from tumnis.core.canonical import CanonicalColumns


class Document(CanonicalColumns, TenantBase, Base):
    """Canonical columns, with the key columns nullable: text entries and uploads have no
    connection or external id."""

    __tablename__ = "documents"

    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("projects.id"))
    title: Mapped[str]
    kind: Mapped[str]
    trust: Mapped[str] = mapped_column(server_default=text("'untrusted'"))
    storage_location_id: Mapped[UUID | None] = mapped_column(ForeignKey("storage_locations.id"))
    path: Mapped[str | None]
    source_revision: Mapped[str | None]
    pinned: Mapped[bool] = mapped_column(server_default=text("false"))
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    connection_id: Mapped[UUID | None] = mapped_column(ForeignKey("connections.id"))  # type: ignore[assignment]
    external_id: Mapped[str | None]  # type: ignore[assignment]
    fetched_at: Mapped[datetime | None]  # type: ignore[assignment]
    role: Mapped[str | None]  # knowledge_0002: "brief" marks the project's pinned brief
    body_md: Mapped[str | None]  # knowledge_0002: a text entry's Markdown body


class StorageLocation(TenantBase, Base):
    """knowledge_0003: where a workspace's files live; S3 keys sealed in `config_enc`."""

    __tablename__ = "storage_locations"

    name: Mapped[str]
    kind: Mapped[str]  # server_path | s3 | sftp
    root: Mapped[str]  # absolute path, or bucket/prefix
    config_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(server_default=text("'online'"))
    status_reason: Mapped[str | None]
    is_default: Mapped[bool] = mapped_column(server_default=text("false"))
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))


class ProjectFolder(TenantBase, Base):
    __tablename__ = "project_folders"

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    location_id: Mapped[UUID] = mapped_column(ForeignKey("storage_locations.id"))
    root_path: Mapped[str]
    mode: Mapped[str] = mapped_column(server_default=text("'tumnis_made'"))
    backup_opt_in: Mapped[bool] = mapped_column(server_default=text("false"))


class DocumentVersion(TenantBase, Base):
    """A snapshot of a text document's body (P1-14 minimal; P1-17 extends it)."""

    __tablename__ = "document_versions"

    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"))
    version_no: Mapped[int]
    content_hash: Mapped[bytes] = mapped_column(LargeBinary)
    body_md: Mapped[str]
    size: Mapped[int] = mapped_column(BigInteger)


class PendingWrite(TenantBase, Base):
    """A note write queued while its location is offline (FR-15.12)."""

    __tablename__ = "pending_writes"

    location_id: Mapped[UUID] = mapped_column(ForeignKey("storage_locations.id"))
    path: Mapped[str]
    document_version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id"))
    if_match: Mapped[str | None]
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    last_error: Mapped[str | None]
