"""The Obsidian note parser (P3-12, FR-15.10): frontmatter, tags, headings, wikilinks,
embeds and Markdown links, from the fixture vault and the link-forms table. Pure: no I/O
besides reading the fixtures here."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

HERE = Path(__file__).parent
VAULT = HERE.parent / "fixtures" / "vault"
LINK_FORMS = HERE / "data" / "obsidian_links.yaml"


def _note(rel: str) -> Any:
    _parse = importlib.import_module("tumnis.modules.knowledge.obsidian.parse")
    parse_note = _parse.parse_note

    return parse_note(rel, (VAULT / rel).read_text(encoding="utf-8"))


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
@pytest.mark.xfail(strict=True, reason="spec:P3-12")
def test_frontmatter_tags_headings() -> None:
    """T-P3-12-01
    Fixture notes parse to the expected frontmatter (a safe YAML load), the lowercased tag
    set (frontmatter `tags` as a list or a string, plus inline `#tag` and `#area/sub`, never
    from code blocks, code spans, URLs or heading markers, and never all digits) and the
    ATX headings outside code blocks; the body has the frontmatter removed. Invalid YAML
    keeps the note and flags `frontmatter_error`.
    """
    _parse = importlib.import_module("tumnis.modules.knowledge.obsidian.parse")
    parse_note = _parse.parse_note

    kickoff = _note("Clients/Acme/Kickoff.md")
    assert kickoff.path == "Clients/Acme/Kickoff.md"
    assert kickoff.title == "Kickoff"
    assert kickoff.frontmatter == {
        "aliases": ["Acme kickoff"],
        "tags": ["meeting", "Client/Acme"],
        "status": "draft",
    }
    assert kickoff.frontmatter_error is False
    assert kickoff.tags == frozenset({"meeting", "client/acme", "followup"})
    assert kickoff.headings == ((1, "Kickoff"), (2, "Scope"))
    assert kickoff.body.startswith("# Kickoff\n")
    assert "tags:" not in kickoff.body

    rates = _note("Inbox/Rates.md")
    assert rates.frontmatter["tumnis_project"] == "acme"
    assert rates.tags == frozenset({"rates"})
    assert rates.headings == ((1, "Rates"), (2, "Pricing"), (2, "Terms"))

    launch = _note("Ideas/Launch.md")
    assert launch.frontmatter == {}
    assert launch.tags == frozenset({"tumnis/lab", "idea/launch"})  # not "section" (a URL)

    clipping = _note("Clippings/Article.md")
    assert clipping.tags == frozenset({"clipping"})

    inline = parse_note(
        "Notes/Inline.md",
        "# Heading\n#1984 #y1984 #Mixed_Case-tag a#b\n"
        "~~~\n## Not a heading\n#fenced\n~~~\n#hashtag-only\n####### Seven is text\n",
    )
    assert inline.tags == frozenset({"y1984", "mixed_case-tag", "hashtag-only"})
    assert inline.headings == ((1, "Heading"),)

    broken = parse_note("Notes/Broken.md", "---\ntags: [unclosed\n---\n# Still here\n")
    assert broken.frontmatter_error is True
    assert broken.frontmatter == {}
    assert broken.headings == ((1, "Still here"),)
    assert broken.body == "# Still here\n"

    not_a_mapping = parse_note("Notes/List.md", "---\n- a\n- b\n---\nText\n")
    assert not_a_mapping.frontmatter_error is True
    assert not_a_mapping.body == "Text\n"

    unsafe = parse_note(
        "Notes/Unsafe.md", "---\nx: !!python/object/apply:os.system ['true']\n---\n"
    )
    assert unsafe.frontmatter_error is True


def _rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = yaml.safe_load(LINK_FORMS.read_text(encoding="utf-8"))
    return rows


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
@pytest.mark.xfail(strict=True, reason="spec:P3-12")
def test_wikilinks_and_embeds() -> None:
    """T-P3-12-02
    Every row of the link-forms table (`tests/unit/data/obsidian_links.yaml`) parses to its
    Link objects (target, heading, block, alias, embed): wikilinks with headings, blocks and
    aliases, embeds with sizes and PDF pages, Markdown links inside the vault resolved
    against the note's folder, and nothing for external URLs, paths leaving the vault,
    unclosed brackets or code spans. The fixture Kickoff note links to Rates (heading
    `Pricing`), Ideas/Launch and Inbox/Rates.md, and embeds diagram.png.
    """
    _parse = importlib.import_module("tumnis.modules.knowledge.obsidian.parse")
    Link = _parse.Link  # noqa: N806
    parse_note = _parse.parse_note

    def link(**fields: Any) -> Any:
        return Link(
            target=fields["target"],
            heading=fields.get("heading"),
            block=fields.get("block"),
            alias=fields.get("alias"),
            embed=fields.get("embed", False),
        )

    for row in _rows():
        note = parse_note("Notes/Here.md", row["text"] + "\n")
        expected = [link(**fields) for fields in row["links"]]
        got = [*note.links, *note.embeds]
        assert sorted(got, key=repr) == sorted(expected, key=repr), row["text"]
        assert all(not item.embed for item in note.links), row["text"]
        assert all(item.embed for item in note.embeds), row["text"]

    kickoff = _note("Clients/Acme/Kickoff.md")
    assert kickoff.links == (
        Link(target="Rates", heading="Pricing", block=None, alias="the rate card", embed=False),
        Link(target="Ideas/Launch", heading=None, block=None, alias=None, embed=False),
        Link(target="Inbox/Rates.md", heading=None, block=None, alias="old rates", embed=False),
    )
    assert kickoff.embeds == (
        Link(target="diagram.png", heading=None, block=None, alias="300", embed=True),
    )


_FRONTMATTERISH = st.lists(
    st.sampled_from(
        ["---", "---\n", "tags: [a", "tags: a", ": :", "- x", "!!python/name:os", "{", "\t", "#"]
    ),
    max_size=8,
).map("\n".join)


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
@pytest.mark.xfail(strict=True, reason="spec:P3-12")
@settings(deadline=None, database=None, max_examples=300)
@given(head=_FRONTMATTERISH, text=st.text())
def test_parse_never_raises(head: str, text: str) -> None:
    """T-P3-12-03
    Hypothesis: any text, with or without malformed frontmatter in front of it, parses to
    a ParsedNote (never an exception), whose tags are lowercased and whose links all have
    a string target.
    """
    _parse = importlib.import_module("tumnis.modules.knowledge.obsidian.parse")
    ParsedNote = _parse.ParsedNote  # noqa: N806
    parse_note = _parse.parse_note

    note = parse_note("Notes/Random.md", head + "\n" + text)
    assert isinstance(note, ParsedNote)
    assert all(tag == tag.lower() for tag in note.tags)
    assert all(isinstance(item.target, str) for item in (*note.links, *note.embeds))
