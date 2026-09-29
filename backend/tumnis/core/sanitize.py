"""One allow-list sanitizer for every piece of outside HTML (P0-16, SEC-4), on
[nh3](https://nh3.readthedocs.io/) (Rust ammonia on html5ever).

HTML is sanitized on ingest (connectors call `sanitize_html` in `map`) and again on every
API read that returns HTML, so a tightened allow-list applies to old rows. The frontend
renders HTML only through `SafeHtml`, which receives the server-sanitized string.

`img` is not allowed: remote images are blocked (SEC-4 allows proxying or blocking;
proxying is later work). Links keep only http, https and mailto URLs and get
`rel="noopener noreferrer nofollow"`.
"""

import html as html_lib
import re
from typing import Final

import nh3

ALLOWED_TAGS: Final = frozenset(
    {
        "a", "b", "strong", "i", "em", "u", "s", "p", "br", "hr", "span", "div",
        "ul", "ol", "li", "blockquote", "code", "pre", "h1", "h2", "h3", "h4", "h5", "h6",
        "table", "thead", "tbody", "tr", "th", "td",
    }
)  # fmt: skip
ALLOWED_ATTRS: Final = {
    "a": frozenset({"href", "title"}),
    "th": frozenset({"colspan", "rowspan"}),
    "td": frozenset({"colspan", "rowspan"}),
}
DROP_WITH_CONTENT: Final = frozenset(
    {
        "script", "style", "iframe", "object", "embed", "noscript", "template", "svg",
        "math", "form", "select", "textarea",
    }
)  # fmt: skip
URL_SCHEMES: Final = frozenset({"http", "https", "mailto"})
LINK_REL: Final = "noopener noreferrer nofollow"
# html5ever re-parses serialized output; a few inputs only settle on the second pass (the
# property test, T-P0-16-05, holds sanitize_html(sanitize_html(x)) == sanitize_html(x)).
_MAX_PASSES: Final = 4


# A URL's scheme part: everything before the first `:` when no `/`, `?` or `#` comes first.
_SCHEME: Final = re.compile(r"^([^/?#]*):")
# What browsers skip inside a URL before reading its scheme.
_URL_SKIPPED: Final = re.compile(r"[\x00-\x20\x7f]+")
_SCRIPT_SCHEMES: Final = ("javascript:", "vbscript:", "data:")


def _attribute_filter(tag: str, attribute: str, value: str) -> str | None:
    """Drop an href whose scheme part is not an allowed scheme, even where ammonia would
    read it as a relative URL (`%6A...:`, `\\x6A...:`): a colon before the first path
    separator is never needed by a link Tumnis keeps. Any other kept attribute (`title`)
    that reads as a script URL is dropped too, so no output ever holds one."""
    normalized = _URL_SKIPPED.sub("", value).lower()
    if normalized.startswith(_SCRIPT_SCHEMES):
        return None
    if tag == "a" and attribute == "href":
        scheme = _SCHEME.match(normalized)
        if scheme is not None and scheme.group(1) not in URL_SCHEMES:
            return None
    return value


def _clean(html: str) -> str:
    return nh3.clean(
        html,
        tags=set(ALLOWED_TAGS),
        clean_content_tags=set(DROP_WITH_CONTENT),
        # "*" empty: no generic attributes (ammonia keeps `lang` and `title` on every tag
        # unless told otherwise).
        attributes={"*": set(), **{tag: set(names) for tag, names in ALLOWED_ATTRS.items()}},
        url_schemes=set(URL_SCHEMES),
        attribute_filter=_attribute_filter,
        strip_comments=True,
        link_rel=LINK_REL,
    )


def sanitize_html(html: str) -> str:
    """Outside HTML reduced to the allow-list: formatting, lists, links, tables and code;
    no script, no event handlers, no styles, no images, no forms."""
    cleaned = _clean(html)
    for _ in range(_MAX_PASSES):
        again = _clean(cleaned)
        if again == cleaned:
            return cleaned
        cleaned = again
    return cleaned


def to_plain_text(html: str) -> str:
    """The words of `html`: every tag dropped (script-like elements with their content),
    entities unescaped and whitespace collapsed. For search text and notifications."""
    stripped = nh3.clean(html, tags=set(), clean_content_tags=set(DROP_WITH_CONTENT))
    return " ".join(html_lib.unescape(stripped).split())
