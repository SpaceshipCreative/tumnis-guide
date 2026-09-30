"""The extraction pipeline's ports (P1-16): the virus scanner, the vision model and the
document extractor. Callers depend on these only; each has a fake beside its real adapter.
"""

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from tumnis.modules.knowledge.rules import DocKind


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
