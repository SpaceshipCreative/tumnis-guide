"""The Obsidian note parser (P3-12, FR-15.10): pure, no I/O.

One note's text becomes a `ParsedNote`: its frontmatter (YAML, safe loader only), tags,
ATX headings, wikilinks, embeds and in-vault Markdown links, and the body without the
frontmatter. Syntax per Obsidian Help (checked 2026-10-01): internal links
(https://obsidian.md/help/links), embeds (https://obsidian.md/help/embeds) and tags
(https://obsidian.md/help/tags).

Code is never content: fenced blocks and inline code spans are masked (blanked to the
same length, so positions keep) before tags and links are read, and headings are only
read outside fences. `parse_note` never raises: frontmatter that is not valid YAML, or not
a mapping, keeps the note and sets `frontmatter_error`.
"""

import posixpath
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import unquote

import yaml

__all__ = ["Link", "ParsedNote", "parse_note"]


@dataclass(frozen=True)
class Link:
    """A link or embed as written: `target` is the note or file named (a wikilink's text
    before `#`, a Markdown link's path resolved against the note's folder), `heading` and
    `block` the part after `#` (`#^id` is a block), `alias` the display text (or an
    image embed's size)."""

    target: str
    heading: str | None = None
    block: str | None = None
    alias: str | None = None
    embed: bool = False


@dataclass(frozen=True)
class ParsedNote:
    path: str
    title: str
    frontmatter: Mapping[str, Any]
    tags: frozenset[str]
    headings: tuple[tuple[int, str], ...]
    links: tuple[Link, ...]
    embeds: tuple[Link, ...]
    body: str  # frontmatter removed
    frontmatter_error: bool = False


# --- The pattern table (named groups; one row per construct) ------------------------------

_FENCE_OPEN: Final = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<info>[^\n]*)$")
_CODE_SPAN: Final = re.compile(r"(?P<ticks>`+)(?P<code>[^`]|[^`][\s\S]*?[^`])(?P=ticks)(?!`)")
_HEADING: Final = re.compile(r"^ {0,3}(?P<marks>#{1,6})(?:[ \t]+(?P<text>[^\n]*?))?[ \t]*$")
_CLOSING_MARKS: Final = re.compile(r"(?:^|[ \t]+)#+[ \t]*$")
_TAG: Final = re.compile(r"(?<![^\s])#(?P<tag>[\w/-]+)")
_WIKILINK: Final = re.compile(r"(?P<bang>!?)\[\[(?P<inner>[^\[\]\n]+?)\]\]")
_MD_LINK: Final = re.compile(
    r"(?P<bang>!?)\[(?P<text>[^\[\]\n]*)\]\((?P<dest><[^<>\n]+>|[^()\s]+)(?:[ \t]+\"[^\"\n]*\")?\)"
)
_SCHEME: Final = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_NOT_ONLY_DIGITS: Final = re.compile(r"[^\d/]")
_TAG_ITEM: Final = re.compile(r"^[\w/-]+$")
_FRONTMATTER_TAG_KEYS: Final = ("tags", "tag")


def parse_note(path: str, text: str) -> ParsedNote:
    """`text` of the note at vault path `path` as a ParsedNote; never raises."""
    frontmatter, body, error = _split_frontmatter(text)
    masked, heading_lines = _mask_code(body)
    headings = tuple(_headings(body, heading_lines))
    found = sorted(_links(path, masked), key=lambda item: item[0])
    links = tuple(link for _, link in found if not link.embed)
    embeds = tuple(link for _, link in found if link.embed)
    tags = _frontmatter_tags(frontmatter) | _inline_tags(masked)
    return ParsedNote(
        path=path,
        title=_title(path),
        frontmatter=frontmatter,
        tags=frozenset(tags),
        headings=headings,
        links=links,
        embeds=embeds,
        body=body,
        frontmatter_error=error,
    )


def _title(path: str) -> str:
    name = posixpath.basename(path)
    return name[:-3] if name.lower().endswith(".md") else name


# --- Frontmatter ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[Mapping[str, Any], str, bool]:
    """(frontmatter, body, error). Frontmatter is the YAML between a first line `---` and
    the next `---` line; without a closing line there is none and the whole text is body."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != "---":
        return {}, text, False
    for end in range(1, len(lines)):
        if lines[end].rstrip() == "---":
            frontmatter, error = _load_yaml("".join(lines[1:end]))
            return frontmatter, "".join(lines[end + 1 :]), error
    return {}, text, False


def _load_yaml(raw: str) -> tuple[Mapping[str, Any], bool]:
    """(mapping, error): the safe loader only; anything but a mapping (or nothing) is an
    error. Any loader failure counts: bad YAML, bad dates, deep nesting."""
    try:
        data = yaml.safe_load(raw)
    except Exception:  # a note's frontmatter can hold anything
        return {}, True
    if data is None:
        return {}, False
    if not isinstance(data, dict):
        return {}, True
    return {str(key): value for key, value in data.items()}, False


def _clean_tag(raw: str) -> str | None:
    tag = raw.strip().removeprefix("#").lower()
    if not tag or not _TAG_ITEM.match(tag) or not _NOT_ONLY_DIGITS.search(tag):
        return None
    return tag


def _frontmatter_tags(frontmatter: Mapping[str, Any]) -> set[str]:
    """`tags` as a list or as a string (comma- or space-separated)."""
    out: set[str] = set()
    for key in _FRONTMATTER_TAG_KEYS:
        value = frontmatter.get(key)
        items: list[Any]
        if isinstance(value, str):
            items = re.split(r"[,\s]+", value)
        elif isinstance(value, list):
            items = value
        else:
            continue
        for item in items:
            if isinstance(item, str) and (tag := _clean_tag(item)):
                out.add(tag)
    return out


# --- Code, headings and tags ---------------------------------------------------------------


def _blank(text: str) -> str:
    """`text` with everything but line breaks turned into spaces (positions kept)."""
    return re.sub(r"[^\n]", " ", text)


def _mask_code(body: str) -> tuple[str, set[int]]:
    """The body with fenced blocks and code spans blanked, and the indexes of the lines
    outside fences (where headings may be)."""
    lines = body.splitlines(keepends=True)
    out: list[str] = []
    outside: set[int] = set()
    fence: str | None = None
    for n, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        if fence is None:
            opened = _FENCE_OPEN.match(stripped)
            if opened and not (opened["fence"][0] == "`" and "`" in opened["info"]):
                fence = opened["fence"]
                out.append(_blank(line))
                continue
            outside.add(n)
            out.append(line)
            continue
        closing = stripped.strip()
        if closing and set(closing) == {fence[0]} and len(closing) >= len(fence):
            fence = None
        out.append(_blank(line))
    masked = "".join(out)
    masked = _CODE_SPAN.sub(lambda m: _blank(m.group(0)), masked)
    return masked, outside


def _headings(body: str, outside: set[int]) -> Iterator[tuple[int, str]]:
    for n, line in enumerate(body.splitlines()):
        if n not in outside:
            continue
        found = _HEADING.match(line)
        if found is None:
            continue
        text = _CLOSING_MARKS.sub("", found["text"] or "").strip()
        if text:
            yield len(found["marks"]), text


def _inline_tags(masked: str) -> set[str]:
    out: set[str] = set()
    for found in _TAG.finditer(masked):
        if tag := _clean_tag(found["tag"].rstrip("/")):
            out.add(tag)
    return out


# --- Links ---------------------------------------------------------------------------------


def _split_target(target: str) -> tuple[str, str | None, str | None]:
    """`Target#Heading` -> (Target, Heading, None); `Target#^id` -> (Target, None, id)."""
    name, sep, sub = target.partition("#")
    if not sep:
        return name.strip(), None, None
    sub = sub.strip()
    if sub.startswith("^"):
        return name.strip(), None, sub[1:].strip() or None
    return name.strip(), sub or None, None


def _wikilink(inner: str, embed: bool) -> Link:
    target, sep, alias = inner.replace("\\|", "|").partition("|")
    name, heading, block = _split_target(target)
    return Link(
        target=name,
        heading=heading,
        block=block,
        alias=(alias.strip() or None) if sep else None,
        embed=embed,
    )


def _in_vault(note_path: str, dest: str) -> str | None:
    """A Markdown link's path as a vault path, None when it leaves the vault."""
    if dest.startswith("/"):
        joined = dest.lstrip("/")
    else:
        joined = posixpath.join(posixpath.dirname(note_path), dest)
    normal = posixpath.normpath(joined)
    if normal in {".", ""} or normal == ".." or normal.startswith("../"):
        return None
    return normal


def _markdown_link(note_path: str, text: str, dest: str, embed: bool) -> Link | None:
    dest = dest.removeprefix("<").removesuffix(">").strip()
    if not dest or dest.startswith("#") or _SCHEME.match(dest):
        return None
    path, _, fragment = dest.partition("#")
    vault_path = _in_vault(note_path, unquote(path))
    if vault_path is None:
        return None
    fragment = unquote(fragment).strip()
    heading: str | None = fragment or None
    block: str | None = None
    if fragment.startswith("^"):
        heading, block = None, fragment[1:] or None
    return Link(
        target=vault_path,
        heading=heading,
        block=block,
        alias=text.strip() or None,
        embed=embed,
    )


def _links(note_path: str, masked: str) -> Iterator[tuple[int, Link]]:
    """Every link and embed with its position; wikilinks are blanked before Markdown
    links are read, so `[[a]](b)` is never read twice."""
    for found in _WIKILINK.finditer(masked):
        yield found.start(), _wikilink(found["inner"], embed=bool(found["bang"]))
    rest = _WIKILINK.sub(lambda m: _blank(m.group(0)), masked)
    for found in _MD_LINK.finditer(rest):
        link = _markdown_link(note_path, found["text"], found["dest"], bool(found["bang"]))
        if link is not None:
            yield found.start(), link
