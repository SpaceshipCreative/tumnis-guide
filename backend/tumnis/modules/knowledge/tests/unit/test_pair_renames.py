"""Renames made outside Tumnis keep the Document (P1-15, FR-15.12): a note by its
`tumnis_id`, any other file by an unambiguous content hash."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
NOTE = UUID("0190a7a0-0000-7000-8000-00000000000a")
PDF = UUID("0190a7a0-0000-7000-8000-00000000000b")
TWIN = UUID("0190a7a0-0000-7000-8000-00000000000c")


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-15")
@pytest.mark.xfail(strict=True, reason="spec:P1-15")
def test_pairs_by_tumnis_id_then_hash() -> None:
    """T-P1-15-07
    A note renamed and edited outside pairs with its old path by the `tumnis_id` in its
    frontmatter (its hash changed); an unedited file pairs by its content hash; a missing
    file with two identical candidates does not pair (trash plus create, never a wrong
    pair). `read_tumnis_id` reads the id from a note's frontmatter.
    """
    from tumnis.modules.knowledge.sync_rules import (  # type: ignore[import-untyped]  # red until P1-15 lands  # noqa: PLC0415
        Prev,
        Remote,
        pair_renames,
        read_tumnis_id,
    )

    def prev(path: str, doc: UUID, digest: str) -> Prev:
        return Prev(
            path=path,
            size=5,
            mtime=T0,
            etag=digest,
            content_hash=digest,
            origin="tumnis",
            document_id=doc,
            synced_version=1,
        )

    def remote(digest: str) -> Remote:
        return Remote(size=5, mtime=T0, etag=digest, content_hash=digest)

    head = f"---\ntumnis_id: {NOTE}\n---\n# Roadmap\n"
    assert read_tumnis_id(head) == NOTE
    assert read_tumnis_id("# no frontmatter\n") is None
    assert read_tumnis_id("---\ntumnis_id: not-a-uuid\n---\n") is None

    missing = [
        prev("notes/Plan.md", NOTE, "h-note-old"),
        prev("uploads/a.pdf", PDF, "h-pdf"),
        prev("uploads/c.pdf", TWIN, "h-twin"),
    ]
    new = [
        ("notes/Roadmap.md", remote("h-note-new"), read_tumnis_id(head)),
        ("uploads/b.pdf", remote("h-pdf"), None),
        ("archive/c1.pdf", remote("h-twin"), None),
        ("archive/c2.pdf", remote("h-twin"), None),
    ]
    assert sorted(pair_renames(missing, new)) == [
        ("notes/Plan.md", "notes/Roadmap.md"),
        ("uploads/a.pdf", "uploads/b.pdf"),
    ]
