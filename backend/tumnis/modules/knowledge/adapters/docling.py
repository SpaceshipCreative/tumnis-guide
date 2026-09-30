"""`DoclingExtractor`: Docling behind the `Extractor` port (P1-16, FR-15.2, ADR-0007).

`convert` runs the standard pipeline (`extraction.standard_converter`) on the scratch copy
and keeps the whole `DoclingDocument` as JSON, its Markdown export, Docling's (mean, low)
grade per page and the number of items on each page, so the pipeline can pick the pages
the vision model should read (`rules.low_confidence_pages`). `chunk` rebuilds the document
from that JSON and chunks it with the `HybridChunker`; `chunk_markdown` does the same for a
page the vision model read; `page_image` renders one PDF page for it. Each chunk keeps its
heading path, the lowest and highest page its items came from (`rules.chunk_pages`), `text`
for display and the contextualized `context_text` for embeddings (P3-10).

Everything here blocks, so the pipeline calls it in a thread. Docling, transformers,
pypdfium2 and Pillow are imported inside the methods: they live only in the extract
worker's image, and importing this module stays free.
"""

import io
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tumnis.modules.knowledge import extraction
from tumnis.modules.knowledge.adapters.port import ChunkRow, Conversion
from tumnis.modules.knowledge.rules import DocKind, chunk_pages

if TYPE_CHECKING:
    from docling.document_converter import DocumentConverter
    from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
    from docling_core.types.doc.document import DoclingDocument

PAGE_IMAGE_SCALE = 2.0  # 144 dpi: what Docling's own VLM pipeline renders pages at


def to_chunk_rows(doc: "DoclingDocument", chunker: "HybridChunker") -> list[ChunkRow]:
    """The document's chunks in order: text, contextualized text, heading path and the
    pages its items came from (None for formats without pages)."""
    rows: list[ChunkRow] = []
    for i, chunk in enumerate(chunker.chunk(dl_doc=doc)):
        meta: Any = chunk.meta
        pages = [prov.page_no for item in meta.doc_items for prov in (item.prov or [])]
        page_from, page_to = chunk_pages(pages)
        rows.append(
            ChunkRow(
                ordinal=i,
                text=chunk.text,
                context_text=chunker.contextualize(chunk=chunk),
                heading_path=list(meta.headings or []),
                page_from=page_from,
                page_to=page_to,
            )
        )
    return rows


class DoclingExtractor:
    def __init__(self, *, chunk_tokenizer: str, max_tokens: int = 512) -> None:
        self._tokenizer_id = chunk_tokenizer
        self._max_tokens = max_tokens
        self._converter: DocumentConverter | None = None
        self._chunker: HybridChunker | None = None

    def _convert_with(self) -> "DocumentConverter":
        if self._converter is None:  # the models load once per process
            self._converter = extraction.standard_converter()
        return self._converter

    def _chunk_with(self) -> "HybridChunker":
        if self._chunker is None:
            self._chunker = extraction.make_chunker(self._tokenizer_id, self._max_tokens)
        return self._chunker

    def convert(self, path: Path, kind: DocKind) -> Conversion:
        from docling.datamodel.base_models import InputFormat  # noqa: PLC0415

        converter = self._convert_with()
        if kind in extraction.FILE_KINDS:
            result = converter.convert(path)
        else:  # plain text: read as Markdown, which it is a subset of
            text = path.read_text(encoding="utf-8", errors="replace")
            result = converter.convert_string(text, format=InputFormat.MD, name=path.stem)
        doc = result.document
        return Conversion(
            doc_json=json.dumps(doc.export_to_dict()).encode(),
            markdown=doc.export_to_markdown(),
            grades=dict(extraction.page_grades(result.confidence)),
            text_items=extraction.text_items_per_page(doc),
        )

    def chunk(self, doc_json: bytes) -> list[ChunkRow]:
        from docling_core.types.doc.document import DoclingDocument  # noqa: PLC0415

        doc = DoclingDocument.model_validate_json(doc_json)
        return to_chunk_rows(doc, self._chunk_with())

    def chunk_markdown(self, markdown: str) -> list[ChunkRow]:
        from docling.datamodel.base_models import InputFormat  # noqa: PLC0415

        result = self._convert_with().convert_string(markdown, format=InputFormat.MD, name="page")
        return to_chunk_rows(result.document, self._chunk_with())

    def page_image(self, path: Path, page: int) -> bytes:
        """Page `page` (1-based) as a PNG: a PDF page rendered, or an image file itself."""
        import pypdfium2  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        image: Any
        if path.suffix.lower() == ".pdf":
            pdf = pypdfium2.PdfDocument(str(path))
            try:
                image = pdf[page - 1].render(scale=PAGE_IMAGE_SCALE).to_pil()
            finally:
                pdf.close()
        else:
            image = Image.open(path)
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
        return buffer.getvalue()
