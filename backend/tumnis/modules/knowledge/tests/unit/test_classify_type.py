"""The upload allow-list (P1-16, SEC-10): the type comes from the content (the sniffed MIME);
the name's extension must be one the sniffed type allows."""

from __future__ import annotations

import pytest

from tumnis.modules.knowledge.rules import (
    ALLOWED_TYPES,
    MAX_UPLOAD_BYTES,
    Refusal,
    classify_type,
)

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
MISMATCH = Refusal("type_mismatch")
NOT_ALLOWED = Refusal("type_not_allowed")

CASES = [
    # (sniffed MIME, file name, expected kind or refusal)
    ("application/pdf", "report.pdf", "pdf"),
    ("application/pdf", "REPORT.PDF", "pdf"),
    ("application/pdf", "archive.tar.pdf", "pdf"),
    ("application/pdf", "report.bin", MISMATCH),
    ("application/pdf", "report", MISMATCH),
    ("application/pdf", "report.pdf.exe", MISMATCH),
    ("text/html", "invoice.pdf", MISMATCH),
    ("text/html", "page.html", "html"),
    ("text/html", "page.htm", "html"),
    (DOCX, "brief.docx", "docx"),
    (DOCX, "brief.doc", MISMATCH),
    (XLSX, "budget.xlsx", "xlsx"),
    (PPTX, "kickoff.pptx", "pptx"),
    (PPTX, "kickoff.docx", MISMATCH),
    ("text/plain", "notes.md", "markdown"),
    ("text/plain", "notes.markdown", "markdown"),
    ("text/plain", "notes.txt", "text"),
    ("text/plain", "rates.csv", "csv"),
    ("text/plain", "script.sh", MISMATCH),
    ("text/markdown", "notes.md", "markdown"),
    ("text/markdown", "notes.txt", MISMATCH),
    ("text/csv", "rates.csv", "csv"),
    ("application/csv", "rates.csv", "csv"),
    ("image/png", "receipt.png", "image"),
    ("image/png", "receipt.jpg", MISMATCH),
    ("image/jpeg", "photo.jpg", "image"),
    ("image/jpeg", "photo.JPEG", "image"),
    ("image/tiff", "scan.tif", "image"),
    ("image/tiff", "scan.tiff", "image"),
    ("image/webp", "shot.webp", "image"),
    ("application/x-dosexec", "setup.exe", NOT_ALLOWED),
    ("application/x-dosexec", "setup.pdf", NOT_ALLOWED),
    ("application/zip", "brief.docx", NOT_ALLOWED),
    ("application/x-shellscript", "run.pdf", NOT_ALLOWED),
    ("image/svg+xml", "logo.svg", NOT_ALLOWED),
    ("application/octet-stream", "blob.pdf", NOT_ALLOWED),
    ("", "empty.txt", NOT_ALLOWED),
]


@pytest.mark.req("SEC-10")
@pytest.mark.wp("P1-16")
@pytest.mark.parametrize(
    ("mime", "filename", "expected"),
    CASES,
    ids=[f"{mime or 'none'}-{name}" for mime, name, _ in CASES],
)
def test_allow_list(mime: str, filename: str, expected: str | Refusal) -> None:
    """T-P1-16-04
    A sniffed MIME outside ALLOWED_TYPES is refused `type_not_allowed`; an allowed MIME
    under a name whose extension it does not allow is refused `type_mismatch` (the last
    suffix counts, in any case); otherwise the kind (pdf, docx, xlsx, pptx, markdown, text,
    csv, html, image). The upload limit is 50 MiB.
    """
    assert classify_type(mime, filename) == expected
    assert MAX_UPLOAD_BYTES == 52_428_800
    assert set(ALLOWED_TYPES) >= {"application/pdf", DOCX, XLSX, PPTX, "text/plain"}
