"""knowledge SQLAlchemy tables owned by this module (mirrors of revisions knowledge_0001
to knowledge_0007)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Computed, ForeignKey, LargeBinary, Text, text
from sqlalchemy import cast as sql_cast
from sqlalchemy import text as sql_text  # `Chunk.text` shadows `text` inside its class body
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import BindParameter, ColumnElement
from sqlalchemy.types import UserDefinedType

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
    status: Mapped[str] = mapped_column(server_default=text("'ready'"))  # knowledge_0006
    status_reason: Mapped[str | None]
    current_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("document_versions.id"))


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
    last_sync_at: Mapped[datetime | None]  # knowledge_0005: the last folder sync's end


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
    body_md: Mapped[str]  # an upload's is '' until its Markdown export is stored
    size: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(server_default=text("'ready'"))  # knowledge_0006
    status_reason: Mapped[str | None]
    source_name: Mapped[str | None]  # the name an upload or a folder file arrived under
    mime: Mapped[str | None]  # what libmagic sniffed
    docling_json: Mapped[bytes | None] = mapped_column(LargeBinary)  # gzip


class ExtractionArtifact(TenantBase, Base):
    """What one extraction step hands the next (knowledge_0006): the converted document,
    its Markdown, the vision pages, the chunks; one row per (version, stage)."""

    __tablename__ = "extraction_artifacts"

    version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id"))
    stage: Mapped[str]
    data: Mapped[bytes] = mapped_column(LargeBinary)


class Chunk(TenantBase, Base):
    """A searchable piece of a document version (knowledge_0006); `tsv` is generated from
    the heading path and the text."""

    __tablename__ = "chunks"

    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"))
    document_version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id"))
    ordinal: Mapped[int]
    text: Mapped[str]
    context_text: Mapped[str]
    heading_path: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=sql_text("'{}'"))
    page_from: Mapped[int | None]
    page_to: Mapped[int | None]
    extractor: Mapped[str] = mapped_column(server_default=sql_text("'docling'"))
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("chunk_tsv(heading_path, text)", persisted=True)
    )


class PendingWrite(TenantBase, Base):
    """A note write queued while its location is offline (FR-15.12)."""

    __tablename__ = "pending_writes"

    location_id: Mapped[UUID] = mapped_column(ForeignKey("storage_locations.id"))
    path: Mapped[str]
    document_version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id"))
    if_match: Mapped[str | None]
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    last_error: Mapped[str | None]


class FolderFile(TenantBase, Base):
    """knowledge_0005: a file the folder sync knows on a location, as of the last sync
    (P1-15); `content_hash` is sha256 hex, `origin` says who made the file."""

    __tablename__ = "folder_files"

    location_id: Mapped[UUID] = mapped_column(ForeignKey("storage_locations.id"))
    path: Mapped[str]
    size: Mapped[int] = mapped_column(BigInteger)
    mtime: Mapped[datetime]
    content_hash: Mapped[str]
    etag: Mapped[str]
    origin: Mapped[str]  # tumnis | external
    document_id: Mapped[UUID | None] = mapped_column(ForeignKey("documents.id"))
    synced_version: Mapped[int | None]
    delete_confirmed: Mapped[bool] = mapped_column(server_default=text("false"))
    last_op: Mapped[str | None]


class Vector(UserDefinedType[list[float]]):
    """pgvector's untyped `vector` (knowledge_0007): written as its text form `[x, y, ...]`
    cast to `vector`, read back from that text form."""

    cache_ok = True

    def get_col_spec(self, **_kw: Any) -> str:
        return "vector"

    def bind_processor(self, dialect: Any) -> Any:
        def process(value: list[float] | None) -> str | None:
            return None if value is None else vector_literal(value)

        return process

    def bind_expression(self, bindvalue: BindParameter[Any]) -> ColumnElement[Any]:
        return sql_cast(bindvalue, self)

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        def process(value: str | None) -> list[float] | None:
            if value is None:
                return None
            return [float(x) for x in value.strip("[]").split(",") if x]

        return process


def vector_literal(values: list[float]) -> str:
    """pgvector's text form of a vector: `[0.1,0.2]`."""
    return "[" + ",".join(repr(float(x)) for x in values) + "]"


class EmbeddingModel(TenantBase, Base):
    """knowledge_0007: an embedding model of the workspace and its state (building, active,
    retired); `index_name` once its partial HNSW index exists (P3-10)."""

    __tablename__ = "embedding_models"

    model: Mapped[str]
    dims: Mapped[int]
    provider: Mapped[str]
    status: Mapped[str]
    index_name: Mapped[str | None]


class Embedding(TenantBase, Base):
    """knowledge_0007: one chunk's vector from one model (P3-10, R-37)."""

    __tablename__ = "embeddings"

    chunk_id: Mapped[UUID] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"))
    project_id: Mapped[UUID | None]
    model: Mapped[str]
    embedding: Mapped[list[float]] = mapped_column(Vector())
