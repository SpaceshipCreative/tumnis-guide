"""One allow-list sanitizer for every piece of outside HTML (P0-16, SEC-4).

The corpus lives in backend/fixtures/xss: `vectors.txt` holds one hostile vector per line
(OWASP filter-evasion cases plus mutation XSS), `keep/<case>.html` pairs with
`keep/<case>.expected.html` for formatting that must survive. Output is judged by walking
it with the stdlib parser, never by the sanitizer's own opinion.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

XSS = Path(__file__).resolve().parents[4] / "fixtures" / "xss"
VECTORS = [
    line
    for line in (XSS / "vectors.txt").read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.startswith("#")
]
KEEP_CASES = sorted(
    path.name.removesuffix(".html")
    for path in (XSS / "keep").glob("*.html")
    if not path.name.endswith(".expected.html")
)
MIN_VECTORS = 120  # plan default

# The allow-list, spelled out here so the test does not trust the module it checks.
SAFE_TAGS = frozenset(
    {
        "a", "b", "strong", "i", "em", "u", "s", "p", "br", "hr", "span", "div",
        "ul", "ol", "li", "blockquote", "code", "pre", "h1", "h2", "h3", "h4", "h5", "h6",
        "table", "thead", "tbody", "tr", "th", "td",
    }
)  # fmt: skip
SAFE_ATTRS = {
    "a": frozenset({"href", "title", "rel"}),
    "th": frozenset({"colspan", "rowspan"}),
    "td": frozenset({"colspan", "rowspan"}),
}
SAFE_SCHEMES = ("http:", "https:", "mailto:")
# Whitespace and control characters browsers skip inside a URL scheme.
_SKIPPED = re.compile(r"[\x00-\x20\x7f]+")


class _Walker(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in SAFE_TAGS:
            self.problems.append(f"tag <{tag}>")
        for name, value in attrs:
            if name.startswith("on"):
                self.problems.append(f"handler {name}= on <{tag}>")
            if name == "style":
                self.problems.append(f"style= on <{tag}>")
            if name not in SAFE_ATTRS.get(tag, frozenset()):
                self.problems.append(f"attribute {name}= on <{tag}>")
            url = _SKIPPED.sub("", html.unescape(value or "")).lower()
            if url.startswith(("javascript:", "vbscript:", "data:")):
                self.problems.append(f"{url[:12]} URL in {name}= on <{tag}>")
            if name == "href" and ":" in url.split("/")[0] and not url.startswith(SAFE_SCHEMES):
                self.problems.append(f"scheme {url.split(':')[0]}: in href")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def unsafe_parts(output: str) -> list[str]:
    """What in `output` could run script or load outside content; empty when safe."""
    walker = _Walker()
    walker.feed(output)
    walker.close()
    return walker.problems


def test_corpus_is_large_enough() -> None:
    """The corpus the spec tests run on (not a spec test itself)."""
    assert len(VECTORS) >= MIN_VECTORS, len(VECTORS)
    assert len(VECTORS) == len(set(VECTORS)), "duplicate vectors"
    assert KEEP_CASES
    for case in KEEP_CASES:
        assert (XSS / "keep" / f"{case}.expected.html").is_file(), case


@pytest.mark.req("SEC-4")
@pytest.mark.wp("P0-16")
@pytest.mark.parametrize("vector", VECTORS, ids=[f"{n:03d}" for n in range(1, len(VECTORS) + 1)])
def test_every_xss_vector_is_neutralized(vector: str) -> None:
    """T-P0-16-03
    Each line of vectors.txt sanitizes to output with no script-capable tag, no `on*`
    attribute, no `style` attribute and no `javascript:`, `vbscript:` or `data:` URL,
    judged by walking the output with `html.parser`.
    """
    from tumnis.core.sanitize import sanitize_html  # noqa: PLC0415

    output = sanitize_html(vector)
    assert unsafe_parts(output) == [], output


@pytest.mark.req("SEC-4")
@pytest.mark.wp("P0-16")
@pytest.mark.parametrize("case", KEEP_CASES)
def test_allowed_formatting_is_kept(case: str) -> None:
    """T-P0-16-04
    Each keep/<case>.html sanitizes to exactly keep/<case>.expected.html (lists, links with
    rel, tables, code blocks, headings and quotes).
    """
    from tumnis.core.sanitize import sanitize_html  # noqa: PLC0415

    source = (XSS / "keep" / f"{case}.html").read_text(encoding="utf-8").rstrip("\n")
    expected = (XSS / "keep" / f"{case}.expected.html").read_text(encoding="utf-8")
    assert sanitize_html(source) == expected.rstrip("\n")


# --- A grammar of hostile HTML for the property test --------------------------------------

TAGS = (
    *sorted(SAFE_TAGS),
    "script", "style", "iframe", "object", "embed", "noscript", "template", "svg", "math",
    "form", "select", "textarea", "img", "video", "audio", "source", "link", "meta", "base",
    "input", "button", "frameset", "frame", "foreignObject", "animate", "set", "xmp",
    "plaintext", "noembed", "noframes", "title", "option", "col", "colgroup", "caption",
    "details", "marquee", "isindex", "x-custom",
)  # fmt: skip
ATTRS = (
    "href", "src", "title", "colspan", "rowspan", "style", "onclick", "onerror", "onload",
    "onmouseover", "formaction", "action", "srcdoc", "xlink:href", "background", "lowsrc",
    "dynsrc", "data", "class", "id", "rel", "target", "poster", "values", "to", "from",
    "attributeName", "is",
)  # fmt: skip
URL_VALUES = (
    "javascript:alert(1)", "JaVaScRiPt:alert(1)", "java\tscript:alert(1)",
    "jav&#x09;ascript:alert(1)", "&#106;avascript:alert(1)", " javascript:alert(1)",
    "vbscript:msgbox(1)", "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "http://example.com/", "https://example.com/?a=1&b=2", "mailto:a@example.com",
    "//evil.example/", "/relative", "#frag", "expression(alert(1))", "alert(1)", "1",
    "\" onmouseover=\"alert(1)", "'><script>alert(1)</script>", "`x`",
)  # fmt: skip
TEXTS = (
    "hello", "<", ">", "&", "&lt;script&gt;", "-->", "<!--", "]]>", "\"", "'", "`",
    "alert(1)", " ", "\n", "&#60;script&#62;",
)  # fmt: skip


def _attribute() -> st.SearchStrategy[str]:
    quote = st.sampled_from(('"', "'", ""))
    value = st.one_of(st.sampled_from(URL_VALUES), st.sampled_from(TEXTS))
    return st.builds(
        lambda name, q, v: f" {name}={q}{v}{q}" if q or " " not in v else f" {name}={v!r}",
        st.sampled_from(ATTRS),
        quote,
        value,
    )


def _fragment(depth: int) -> st.SearchStrategy[str]:
    text = st.sampled_from(TEXTS)
    if depth == 0:
        return text
    child = st.deferred(lambda: _fragment(depth - 1))
    element = st.builds(
        lambda tag, attrs, inner, close: (
            f"<{tag}{''.join(attrs)}>{''.join(inner)}" + (f"</{tag}>" if close else "")
        ),
        st.sampled_from(TAGS),
        st.lists(_attribute(), max_size=3),
        st.lists(child, max_size=3),
        st.booleans(),
    )
    comment = st.builds(lambda inner: f"<!--{inner}-->", st.sampled_from(TEXTS))
    return st.one_of(text, element, comment)


hostile_html = st.lists(_fragment(3), min_size=1, max_size=4).map("".join)


@pytest.mark.req("SEC-4")
@pytest.mark.wp("P0-16")
@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(hostile_html)
def test_sanitizer_is_idempotent_and_safe_on_random_html(source: str) -> None:
    """T-P0-16-05
    Over a grammar of tags, attributes, URL schemes and comments: sanitizing twice gives the
    same output as sanitizing once, and the output passes the T-P0-16-03 checker.
    """
    from tumnis.core.sanitize import sanitize_html  # noqa: PLC0415

    once = sanitize_html(source)
    assert sanitize_html(once) == once
    assert unsafe_parts(once) == [], (source, once)


@pytest.mark.req("SEC-4")
@pytest.mark.wp("P0-16")
def test_plain_text_drops_markup_and_scripts() -> None:
    """`to_plain_text` keeps the words, drops every tag and the content of script-like
    elements, and unescapes entities (search and notification text)."""
    from tumnis.core.sanitize import to_plain_text  # noqa: PLC0415

    source = "<p>Hi&nbsp;<b>there</b> &amp; welcome</p><script>alert(1)</script><style>p{}</style>"
    assert to_plain_text(source) == "Hi there & welcome"
