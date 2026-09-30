"""Edges of the folder sync's pure rules (P1-15, FR-15.12) beyond the spec table: note
frontmatter round trips, names whose look-alike characters or long suffixes need care, and
rename pairing that has nothing to pair."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from tumnis.modules.knowledge.sync_rules import (
    Prev,
    Remote,
    note_body,
    numbered_name,
    pair_renames,
    read_tumnis_id,
    render_note,
    sanitize_filename,
)

NOTE = UUID("0190a7a0-0000-7000-8000-00000000000a")
T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)

pytestmark = [pytest.mark.req("FR-15.12"), pytest.mark.wp("P1-15")]


def test_a_note_round_trips_through_its_file() -> None:
    """A plain body gets its own frontmatter; `note_body` gives the body back unchanged."""
    text = render_note(NOTE, "# Plan\n")
    assert text == f"---\ntumnis_id: {NOTE}\n---\n# Plan\n"
    assert read_tumnis_id(text) == NOTE
    assert note_body(text) == "# Plan\n"


def test_a_note_with_frontmatter_of_its_own_keeps_it() -> None:
    """The id goes first into existing frontmatter, and leaving it out restores the body."""
    body = "---\ntags: [a]\n---\n# Plan\n"
    text = render_note(NOTE, body)
    assert text == f"---\ntumnis_id: {NOTE}\ntags: [a]\n---\n# Plan\n"
    assert read_tumnis_id(text) == NOTE
    assert note_body(text) == body


def test_files_without_an_id_are_their_own_body() -> None:
    assert note_body("# no frontmatter\n") == "# no frontmatter\n"
    assert note_body("---\ntags: [a]\n---\nx") == "---\ntags: [a]\n---\nx"
    assert read_tumnis_id("---\ntags: [a]\n---\nx") is None
    assert read_tumnis_id(f"---\r\ntumnis_id: '{NOTE}'\r\n---\r\nx") == NOTE


def test_names_made_safe_beyond_the_table() -> None:
    """A character whose compatibility form holds a dot becomes '-'; a suffix too long to
    be an extension is cut with the rest of the name."""
    assert sanitize_filename("v⒈.txt") == "v-.txt"  # U+2488 DIGIT ONE FULL STOP
    long_suffix = "a." + "b" * 300
    cut = sanitize_filename(long_suffix)
    assert len(cut.encode()) == 200
    assert cut.startswith("a.b")
    assert numbered_name("Plan.md", 1) == "Plan.md"


def test_pairing_skips_records_without_a_document() -> None:
    """A record with no Document pairs by hash only; ambiguous hashes pair nothing."""
    orphan = Prev(
        path="uploads/x.pdf",
        size=1,
        mtime=T0,
        etag="h",
        content_hash="h",
        origin="external",
        document_id=None,
        synced_version=None,
    )
    moved = ("uploads/y.pdf", Remote(size=1, mtime=T0, etag="h", content_hash="h"), None)
    assert pair_renames([orphan], [moved]) == [("uploads/x.pdf", "uploads/y.pdf")]
    twin = orphan.model_copy(update={"path": "uploads/z.pdf"})
    assert pair_renames([orphan, twin], [moved]) == []
