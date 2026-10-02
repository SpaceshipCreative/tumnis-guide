"""The extraction pipeline's ports (P1-16): the virus scanner, the vision model and the
document extractor; and an S3 linked source's (P3-13): its read-only listing and the key
capability check. Callers depend on these only; each has a fake beside its real adapter.
"""

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from tumnis.modules.knowledge.rules import DocKind, KeyCapabilities
from tumnis.modules.knowledge.storage import FileStat, Health, Page


class ScanResult(BaseModel, frozen=True):
    infected: bool
    signature: str | None = None  # clamd's name for what it found, e.g. "Win.Test.EICAR_HDB-1"


@runtime_checkable
class Scanner(Protocol):
    async def scan(self, stream: AsyncIterator[bytes]) -> ScanResult: ...


@runtime_checkable
class Vision(Protocol):
    """A page image in, the page's Markdown out (ADR-0007)."""

    async def page_markdown(self, image: bytes, *, page: int) -> str: ...

    async def health(self) -> Literal["ok", "degraded"]: ...


class ChunkRow(BaseModel, frozen=True):
    """One chunk of a document: `text` for display, `context_text` (with its headings) for
    embeddings, `page_from` and `page_to` exact for PDFs and slides, None otherwise."""

    ordinal: int
    text: str
    context_text: str
    heading_path: list[str]
    page_from: int | None
    page_to: int | None
    extractor: Literal["docling", "vlm"] = "docling"


@dataclass(frozen=True)
class Conversion:
    """What converting a file leaves: the structured document (serialized; large, so it
    goes to `extraction_artifacts`), its Markdown, Docling's per-page grade (one value or a
    (mean, low) pair) and the number of text items on each page."""

    doc_json: bytes
    markdown: str
    grades: Mapping[int, str | tuple[str, str]] = field(default_factory=dict)
    text_items: Mapping[int, int] = field(default_factory=dict)


@runtime_checkable
class Extractor(Protocol):
    """Docling behind one seam: everything blocking, so the pipeline runs it in a thread."""

    def convert(self, path: Path, kind: DocKind) -> Conversion: ...

    def chunk(self, doc_json: bytes) -> list[ChunkRow]: ...

    def chunk_markdown(self, markdown: str) -> list[ChunkRow]: ...

    def page_image(self, path: Path, page: int) -> bytes: ...


@runtime_checkable
class S3SourceReader(Protocol):
    """An S3 bucket linked as a knowledge source (P3-13, FR-15.11): read only, never a
    write. Keys are whole object keys in the bucket; `list` pages `ListObjectsV2` (current
    versions only, so a versioned bucket shows its latest version and a delete marker
    shows as the key missing)."""

    async def list(self, prefix: str, cursor: str | None) -> Page[FileStat]: ...

    async def stat(self, key: str) -> FileStat | None: ...

    def read(self, key: str) -> AsyncIterator[bytes]: ...

    async def health(self) -> Health: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class KeyCapabilityCheck(Protocol):
    """What a linked source's key may do, asked of its provider (P3-13): Backblaze B2's
    `b2_authorize_account` and MinIO's account info. Other providers have no check."""

    async def check_b2(
        self, key_id: str, application_key: str, *, bucket: str
    ) -> KeyCapabilities: ...

    async def check_minio(  # where, who, and what the key is for
        self,
        endpoint: str,
        region: str,
        access_key: str,
        secret_key: str,
        *,
        bucket: str,
        prefixes: Sequence[str],
    ) -> KeyCapabilities: ...
