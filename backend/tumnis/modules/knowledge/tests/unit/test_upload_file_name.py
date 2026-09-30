"""How an upload's name is stored (P1-16, SEC-10, FR-15.12): the basename only, nothing a
path, a Windows share or a look-alike can turn into another place, and a number when the
name is taken."""

from __future__ import annotations

import pytest

from tumnis.modules.knowledge.rules import numbered_name, safe_rel_path, upload_file_name

CASES = [
    ("report.pdf", "report.pdf"),
    ("CON.txt", "CON_.txt"),
    ("con", "con_"),
    ("COM3.tar.gz", "COM3_.tar.gz"),
    ("CONSOLE.txt", "CONSOLE.txt"),
    ("a:b?.pdf", "a-b-.pdf"),
    ('q"<>|*.md', "q-----.md"),
    ("../../etc/passwd", "passwd"),
    ("C:\\Users\\me\\brief.docx", "brief.docx"),
    ("  .hidden. ", "hidden"),
    ("tab\there\x00.txt", "tabhere.txt"),
    ("evil\u202egnp.exe", "evilgnp.exe"),
    ("a\u2215b.pdf", "a-b.pdf"),
    ("caf\u0065\u0301.pdf", "caf\u00e9.pdf"),
    ("", "untitled"),
    ("...", "untitled"),
    ("/", "untitled"),
]


@pytest.mark.req("SEC-10")
@pytest.mark.wp("P1-16")
@pytest.mark.parametrize(("name", "expected"), CASES)
def test_upload_file_name(name: str, expected: str) -> None:
    """Basename only, NFC, controls dropped, `/ \\ : * ? " < > |` and look-alike separators
    become '-', spaces and dots trimmed, Windows reserved names get a '_'."""
    stored = upload_file_name(name)
    assert stored == expected
    assert safe_rel_path(f"uploads/{stored}") == f"uploads/{stored}"


@pytest.mark.req("SEC-10")
@pytest.mark.wp("P1-16")
def test_long_names_keep_their_extension() -> None:
    """A name over 200 bytes is cut in the stem, on a character boundary, and keeps its
    extension."""
    stored = upload_file_name("\u00e9" * 300 + ".pdf")
    assert stored.endswith(".pdf")
    assert len(stored.encode()) <= 200
    assert stored.removesuffix(".pdf") == "\u00e9" * ((200 - 4) // 2)


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-16")
@pytest.mark.parametrize(
    ("name", "attempt", "expected"),
    [
        ("a-b-.pdf", 1, "a-b-.pdf"),
        ("a-b-.pdf", 2, "a-b- 2.pdf"),
        ("a-b-.pdf", 3, "a-b- 3.pdf"),
        ("notes.tar.gz", 2, "notes.tar 2.gz"),
        ("README", 2, "README 2"),
    ],
)
def test_numbered_name(name: str, attempt: int, expected: str) -> None:
    """A taken name gets ` 2`, ` 3` before its last suffix (at the end without one)."""
    assert numbered_name(name, attempt) == expected
