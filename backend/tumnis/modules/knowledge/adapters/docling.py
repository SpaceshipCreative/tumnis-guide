"""`DoclingExtractor`: Docling behind the `Extractor` port (P1-16, impl-2).

The interface is fixed here so the extraction-set test (T-P1-16-06) type-checks; the
converter, the chunker and the vision pass arrive with the second implementation PR. Docling
imports stay inside this module and `extraction.py`, so the api image stays light.
"""

from pathlib import Path

from tumnis.modules.knowledge.adapters.port import ChunkRow, Conversion
from tumnis.modules.knowledge.rules import DocKind


class DoclingExtractor:
    def __init__(self, *, chunk_tokenizer: str, max_tokens: int = 512) -> None:
        raise NotImplementedError

    def convert(self, path: Path, kind: DocKind) -> Conversion:
        raise NotImplementedError

    def chunk(self, doc_json: bytes) -> list[ChunkRow]:
        raise NotImplementedError

    def chunk_markdown(self, markdown: str) -> list[ChunkRow]:
        raise NotImplementedError

    def page_image(self, path: Path, page: int) -> bytes:
        raise NotImplementedError
