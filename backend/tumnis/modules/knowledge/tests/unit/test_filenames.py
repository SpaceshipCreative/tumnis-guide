"""Names Tumnis gives files in a project folder (P1-15, FR-15.12, SEC-5): sanitized for every
filesystem the folder may live on, de-duplicated ignoring case, and always a safe path."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

LONG_STEM = "x" * 300
WIDE_STEM = "\u00e9" * 150  # 2 bytes each

# (kind, name, names already taken (casefolded), expected)
CASES = [
    ("sanitize", "CON.txt", None, "CON_.txt"),
    ("sanitize", "nul", None, "nul_"),
    ("sanitize", "LPT9.md", None, "LPT9_.md"),
    ("sanitize", "COM1.tar.gz", None, "COM1_.tar.gz"),
    ("sanitize", "CONSOLE.txt", None, "CONSOLE.txt"),
    ("sanitize", "a:b?.pdf", None, "a-b-.pdf"),
    ("sanitize", 'q"<>|*.md', None, "q-----.md"),
    ("sanitize", "a/b\\c.txt", None, "a-b-c.txt"),
    ("sanitize", "report. ", None, "report"),
    ("sanitize", "  .hidden. ", None, "hidden"),
    ("sanitize", "trailing...", None, "trailing"),
    ("sanitize", "tab\there\x00.txt", None, "tabhere.txt"),
    ("sanitize", "evil\u202egnp.exe", None, "evilgnp.exe"),
    ("sanitize", "cafe\u0301.pdf", None, "caf\u00e9.pdf"),
    ("sanitize", "a\u2215b\uff0ec.md", None, "a-b-c.md"),
    ("sanitize", "~draft.md", None, "-draft.md"),
    ("sanitize", LONG_STEM + ".pdf", None, "x" * 196 + ".pdf"),
    ("sanitize", WIDE_STEM + ".md", None, "\u00e9" * 98 + ".md"),
    ("sanitize", "", None, "untitled"),
    ("sanitize", "...", None, "untitled"),
    ("dedupe", "Plan.md", set(), "Plan.md"),
    ("dedupe", "Plan.md", {"plan.md"}, "Plan 2.md"),
    ("dedupe", "Plan.md", {"plan.md", "plan 2.md"}, "Plan 3.md"),
    ("dedupe", "Makefile", {"makefile"}, "Makefile 2"),
    ("dedupe", ".env", {".env"}, ".env 2"),
    ("dedupe", "notes/Plan.md", {"notes/plan.md"}, "notes/Plan 2.md"),
]


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-15")
@pytest.mark.xfail(strict=True, reason="spec:P1-15")
@pytest.mark.parametrize(
    ("kind", "name", "taken", "expected"),
    CASES,
    ids=[f"{kind}-{i:02}" for i, (kind, *_rest) in enumerate(CASES)],
)
def test_sanitize_and_dedupe(kind: str, name: str, taken: set[str] | None, expected: str) -> None:
    """T-P1-15-05
    `sanitize_filename`: NFC, control and format characters dropped, `/ \\ : * ? " < > |`
    and look-alike separators or dots replaced with '-', spaces and dots trimmed at both
    ends, a leading '~' replaced, Windows reserved names (CON, PRN, AUX, NUL, COM1-9,
    LPT1-9, before any suffix) given a trailing '_', cut to 200 bytes keeping the extension,
    empty -> 'untitled'. `dedupe_name`: a taken name (compared ignoring case) gets ' 2',
    ' 3' before its extension.
    """
    from tumnis.modules.knowledge.sync_rules import (  # type: ignore[import-untyped]  # red until P1-15 lands  # noqa: PLC0415
        dedupe_name,
        sanitize_filename,
    )

    if kind == "sanitize":
        assert sanitize_filename(name) == expected
    else:
        assert taken is not None
        assert dedupe_name(name, frozenset(taken)) == expected


@pytest.mark.req("FR-15.12", "SEC-5")
@pytest.mark.wp("P1-15")
@pytest.mark.xfail(strict=True, reason="spec:P1-15")
@given(name=st.text())
def test_sanitized_names_pass_safe_rel_path(name: str) -> None:
    """T-P1-15-06
    For any text, `safe_rel_path(sanitize_filename(text))` never raises and returns the
    sanitized name unchanged, which is at most 200 bytes.
    """
    from tumnis.modules.knowledge.rules import safe_rel_path  # noqa: PLC0415
    from tumnis.modules.knowledge.sync_rules import sanitize_filename  # noqa: PLC0415

    clean = sanitize_filename(name)
    assert safe_rel_path(clean) == clean
    assert len(clean.encode()) <= 200
