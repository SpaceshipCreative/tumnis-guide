"""The search query a task's passages come from (P1-17, FR-15.4), and how text entries are
cut into chunks by their Markdown headings (P1-17, FR-15.3)."""

from __future__ import annotations

import pytest

from tumnis.modules.knowledge.rules import (
    CHUNK_MAX_CHARS,
    MAX_QUERY_TERMS,
    markdown_sections,
    passage_query,
)


@pytest.mark.req("FR-15.4")
@pytest.mark.wp("P1-17")
def test_query_keeps_distinct_content_words_joined_with_or() -> None:
    """Words of three or more letters, case-folded, stop words and numbers left out, each
    once in first-seen order, from the title, then the criteria, then the goal."""
    query = passage_query(
        "Quote Acme for the redesign",
        ["The quote names the senior designer rate", "Sent by 2026-03-12"],
        "Launch the new site by April",
    )
    assert query == (
        "quote or acme or redesign or names or senior or designer or rate or sent"
        " or launch or new or site or april"
    )


@pytest.mark.req("FR-15.4")
@pytest.mark.wp("P1-17")
def test_query_stops_at_twelve_terms_and_may_be_empty() -> None:
    """At most MAX_QUERY_TERMS terms; nothing usable gives an empty query."""
    words = " ".join(f"term{chr(ord('a') + i)}x" for i in range(20))
    assert len(passage_query(words, [], None).split(" or ")) == MAX_QUERY_TERMS
    assert passage_query("Do it", ["to me"], None) == ""


@pytest.mark.req("FR-15.3")
@pytest.mark.wp("P1-17")
def test_sections_follow_heading_paths() -> None:
    """Each heading opens a section under its parents; code fences hide `#` lines; the
    frontmatter and empty sections are left out."""
    md = (
        "---\ntumnis_id: x\n---\n# Rate notes\n\nIntro.\n\n## Senior designer\n\n160 an hour.\n"
        "\n```sh\n# not a heading\n```\n\n## Terms\n\n### Payment\n\nThirty days.\n"
    )
    assert markdown_sections(md) == [
        (["Rate notes"], "Intro."),
        (["Rate notes", "Senior designer"], "160 an hour.\n\n```sh\n# not a heading\n```"),
        (["Rate notes", "Terms", "Payment"], "Thirty days."),
    ]


@pytest.mark.req("FR-15.3")
@pytest.mark.wp("P1-17")
def test_long_sections_are_cut_at_paragraphs() -> None:
    """A section over CHUNK_MAX_CHARS is cut at blank lines, and a longer paragraph hard."""
    para = "word " * 300  # 1,500 characters
    md = f"No heading.\n\n{para}\n\n{para}\n\n{'x' * (CHUNK_MAX_CHARS + 10)}"
    pieces = markdown_sections(md)
    assert all(path == [] for path, _ in pieces)
    assert all(len(text) <= CHUNK_MAX_CHARS for _, text in pieces)
    assert len(pieces) == 4
    assert pieces[0][1].startswith("No heading.")
