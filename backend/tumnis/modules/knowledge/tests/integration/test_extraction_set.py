"""The extraction set (P1-16, FR-15.2): every fixture file goes through the real Docling
pipeline and must yield the chunks its `<file>.expected.yaml` names. This is the regression
suite for Docling upgrades, so matching is tolerant of chunk boundaries and exact about
page numbers.

Expected file format:

    min_chunks: 1
    max_chunks: 6
    chunks:            # each entry must be matched by at least one chunk
      - contains: ["Senior designer", "160"]     # every snippet appears in its text
        heading_path_endswith: ["Rates"]         # optional
        page_from: 2                             # optional; null asserts "no page"
        page_to: 2
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from tumnis.modules.knowledge.rules import DocKind, classify_type
from tumnis.modules.knowledge.tests._samples import EXTRACTION

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.enable_socket]

FIXTURES = [
    "text-2p.pdf",
    "scanned-1p.pdf",
    "rate-card-table.pdf",
    "two-column.pdf",
    "brief.docx",
    "budget.xlsx",
    "kickoff.pptx",
    "notes.md",
    "rates.csv",
    "receipt.png",
]


def _kind(name: str) -> DocKind:
    import magic  # noqa: PLC0415

    mime = magic.from_file(str(EXTRACTION / name), mime=True)
    kind = classify_type(mime, name)
    assert isinstance(kind, str), (name, mime, kind)
    return kind


def _matches(chunk: Any, want: dict[str, Any]) -> bool:
    if any(snippet not in chunk.text for snippet in want["contains"]):
        return False
    tail = want.get("heading_path_endswith")
    if tail is not None and chunk.heading_path[-len(tail) :] != tail:
        return False
    return all(
        chunk_page == want[key]
        for key, chunk_page in (
            ("page_from", chunk.page_from),
            ("page_to", chunk.page_to),
        )
        if key in want
    )


@pytest.mark.req("FR-15.2")
@pytest.mark.wp("P1-16")
@pytest.mark.xfail(strict=True, reason="spec:P1-16")
@pytest.mark.parametrize("fixture", FIXTURES)
def test_expected_chunks(fixture: str) -> None:
    """T-P1-16-06
    The fixture, converted and chunked by the real Docling extractor, yields between
    `min_chunks` and `max_chunks` chunks, and every entry of its expected file is matched
    by one: snippets present, heading path ending as named, exact pages.
    """
    from tumnis.modules.knowledge.adapters.docling import DoclingExtractor  # noqa: PLC0415

    expected = yaml.safe_load(Path(EXTRACTION / f"{fixture}.expected.yaml").read_text())
    extractor = DoclingExtractor(chunk_tokenizer="sentence-transformers/all-MiniLM-L6-v2")
    conversion = extractor.convert(EXTRACTION / fixture, _kind(fixture))
    chunks = extractor.chunk(conversion.doc_json)

    assert expected["min_chunks"] <= len(chunks) <= expected["max_chunks"]
    for want in expected["chunks"]:
        assert any(_matches(chunk, want) for chunk in chunks), want
