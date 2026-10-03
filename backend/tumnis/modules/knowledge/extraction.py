"""Docling configuration (P1-16, FR-15.2, ADR-0007).

The converter reads PDFs with the standard pipeline: pages with a text layer are parsed,
bitmap regions (scans, photos of pages) are OCR'd, and tables get TableFormer's structure
with cell matching ([usage](https://docling-project.github.io/docling/usage/)). Images use
the same options. Chunks come from the `HybridChunker` with a Hugging Face tokenizer
([chunking](https://docling-project.github.io/docling/concepts/chunking/)); the extractor
maps each chunk to a `ChunkRow` (`adapters/docling.py`).

Every Docling import is inside a function, so importing this module costs nothing: only
the extract worker ever calls these, and the api image does not carry Docling.
"""

from typing import TYPE_CHECKING, Any

from tumnis.modules.knowledge.rules import DocKind

if TYPE_CHECKING:
    from docling.document_converter import DocumentConverter
    from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
    from docling_core.types.doc.document import DoclingDocument

DEFAULT_MAX_TOKENS = 512  # plan default
# Pages Docling's threaded PDF pipeline queues between its stages (its default is 100).
# Under worker-extract's 4 GiB limit the default held so many pages at once that a 210-page
# PDF was killed for memory; with 8, 210 pages peaked at 2.4 GiB in the same time.
PAGES_IN_FLIGHT = 8

# Docling reads the format from the file; these are the kinds it converts from a file.
FILE_KINDS: frozenset[DocKind] = frozenset(
    {"pdf", "docx", "xlsx", "pptx", "markdown", "csv", "html", "image"}
)


def check_max_tokens(max_tokens: int) -> int:
    """`max_tokens`, if it can hold any text; a zero or negative limit is a ValueError."""
    if max_tokens <= 0:
        raise ValueError(f"max_tokens must be positive, got {max_tokens}")
    return max_tokens


def standard_converter() -> "DocumentConverter":
    """The standard pipeline for PDFs and images: OCR on bitmap regions, table structure
    with cell matching."""
    from docling.datamodel.base_models import InputFormat  # noqa: PLC0415
    from docling.datamodel.pipeline_options import (  # noqa: PLC0415
        PdfPipelineOptions,
        TableStructureOptions,
    )
    from docling.document_converter import (  # noqa: PLC0415
        DocumentConverter,
        ImageFormatOption,
        PdfFormatOption,
    )

    pdf = PdfPipelineOptions()
    pdf.do_ocr = True  # OCR applies to bitmap regions; text-layer pages are parsed
    pdf.do_table_structure = True
    pdf.table_structure_options = TableStructureOptions(do_cell_matching=True)
    pdf.queue_max_size = PAGES_IN_FLIGHT  # bounded memory on a long PDF
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pdf),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=pdf),
        }
    )


def make_chunker(tokenizer_id: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> "HybridChunker":
    """A `HybridChunker` whose token limit is counted with `tokenizer_id`'s tokenizer
    (prefetched into the image, never downloaded at run time); undersized peers merge."""
    check_max_tokens(max_tokens)
    from docling_core.transforms.chunker.hybrid_chunker import HybridChunker  # noqa: PLC0415
    from docling_core.transforms.chunker.tokenizer.huggingface import (  # noqa: PLC0415
        HuggingFaceTokenizer,
    )
    from transformers import AutoTokenizer  # noqa: PLC0415

    tokenizer = HuggingFaceTokenizer(
        tokenizer=AutoTokenizer.from_pretrained(tokenizer_id), max_tokens=max_tokens
    )
    return HybridChunker(tokenizer=tokenizer, merge_peers=True)


def page_grades(confidence: Any) -> dict[int, tuple[str, str]]:
    """(mean grade, low grade) per page from a `ConfidenceReport`, as lower-case names
    ([confidence scores](https://docling-project.github.io/docling/concepts/confidence_scores/))."""
    return {
        int(page): (str(scores.mean_grade.value), str(scores.low_grade.value))
        for page, scores in confidence.pages.items()
    }


def text_items_per_page(doc: "DoclingDocument") -> dict[int, int]:
    """How many items (text, tables, pictures) Docling placed on each page; a page it
    found nothing on counts 0."""
    counts: dict[int, int] = {int(page): 0 for page in doc.pages}
    for item, _level in doc.iterate_items():
        for prov in getattr(item, "prov", None) or []:
            counts[prov.page_no] = counts.get(prov.page_no, 0) + 1
    return counts
