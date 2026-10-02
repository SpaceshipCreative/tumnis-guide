"""Wikilink resolution by Obsidian's shortest-path rule (P3-12, FR-15.10): a path written
in full, a path relative to the linking note, else a unique basename; ambiguous or
missing targets stay unresolved."""

from __future__ import annotations

import pytest

from tumnis.modules.knowledge.obsidian.rules import PathIndex, resolve_link

PATHS = {
    "Inbox/Rates.md",
    "Ideas/Launch.md",
    "Clients/Acme/Notes.md",
    "Clients/Beta/Notes.md",
    "Attachments/diagram.png",
    "Clients/Acme/Sub/Deep.md",
}


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
@pytest.mark.parametrize(
    ("target", "from_path", "expected"),
    [
        ("Rates", "Clients/Acme/Kickoff.md", "Inbox/Rates.md"),  # unique basename
        ("rates.md", "Clients/Acme/Kickoff.md", "Inbox/Rates.md"),  # case, suffix
        ("Ideas/Launch", "Clients/Acme/Kickoff.md", "Ideas/Launch.md"),  # full path
        ("Inbox/Rates.md", "x.md", "Inbox/Rates.md"),
        ("diagram.png", "Clients/Acme/Kickoff.md", "Attachments/diagram.png"),
        ("Notes", "Clients/Acme/Kickoff.md", None),  # two "Notes": ambiguous
        ("Acme/Notes", "Inbox/x.md", "Clients/Acme/Notes.md"),  # unique path suffix
        ("Sub/Deep", "Clients/Acme/Kickoff.md", "Clients/Acme/Sub/Deep.md"),  # relative
        ("../Beta/Notes", "Clients/Acme/Kickoff.md", "Clients/Beta/Notes.md"),
        ("Missing note", "Ideas/Launch.md", None),
        ("", "Ideas/Launch.md", None),  # [[#Heading]] in the same note
    ],
)
def test_resolve_link_shortest_path(target: str, from_path: str, expected: str | None) -> None:
    """Each target resolves to the vault path Obsidian would open, or to nothing."""
    assert resolve_link(target, from_path, PATHS) == expected


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
def test_one_path_index_serves_every_link() -> None:
    """A sync builds one `PathIndex` for all its links; it resolves as the plain paths do
    (full path, relative, unique trailing path, unique basename, ambiguous, missing)."""
    index = PathIndex(PATHS)
    for target, from_path in [
        ("Rates", "Clients/Acme/Kickoff.md"),
        ("Ideas/Launch", "x.md"),
        ("Notes", "x.md"),
        ("Acme/Notes", "Inbox/x.md"),
        ("Sub/Deep", "Clients/Acme/Kickoff.md"),
        ("../Beta/Notes", "Clients/Acme/Kickoff.md"),
        ("diagram.png", "x.md"),
        ("Missing note", "x.md"),
    ]:
        assert resolve_link(target, from_path, index) == resolve_link(target, from_path, PATHS)
