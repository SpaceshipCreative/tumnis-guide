"""search pure rules: no I/O, `now` and `tz` passed in (P0-20, FR-3.9).

Query parsing: what the user typed becomes phrases ("double quotes"), complete terms and
the prefix still being typed. Every tsquery operator is stripped first, and the prefix is
letters and digits only, so user text reaches Postgres only as the bound argument of one
of three fixed functions (`tsquery_parts`), never as tsquery syntax.

Ranking constants: `score = ts_rank_cd * (PROJECT_BOOST on a project match)
/ (1 + age_days / RECENCY_DAYS)`. Changing one is a spec-change PR that updates
backend/fixtures/search/corpus.yaml.

Snippets: `mark_cuts` puts an ellipsis where a ts_headline fragment leaves text out
(APP-F07).
"""

import re
from dataclasses import dataclass
from typing import Final

MAX_TERMS: Final = 8  # plan default: complete terms (and phrases) kept per query
PROJECT_BOOST: Final = 2.0  # plan default
RECENCY_DAYS: Final = 14.0  # plan default: score / (1 + age_days / 14)
MIN_PREFIX_CHARS: Final = 2  # plan default: a shorter last token is a complete term

# Characters with meaning in to_tsquery syntax (& | ! ( ) : * < > ' \), and NUL (which
# Postgres text refuses), replaced by spaces.
_OPERATORS: Final = re.compile(r"[&|!():*<>'\\\x00]")
_PHRASE: Final = re.compile(r'"([^"]*)"')
_ALNUM_RUN: Final = re.compile(r"[^\W_]+")  # letters and digits (str.isalnum), no underscore


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    phrases: tuple[str, ...]  # from "double quotes"
    terms: tuple[str, ...]  # complete words, english config
    prefix: str | None  # last unquoted token when the query does not end in a space


def _clean(text: str) -> list[str]:
    return _OPERATORS.sub(" ", text).split()


def parse_query(q: str) -> ParsedQuery:
    """Phrases are the quoted runs; an unmatched quote is dropped. The rest splits on
    whitespace once the operators are gone. When the query does not end in whitespace, its
    last unquoted token is the prefix being typed: its last run of letters and digits (any
    runs before it become terms), unless that run is shorter than MIN_PREFIX_CHARS, when it
    stays a term. At most MAX_TERMS phrases and MAX_TERMS terms are kept."""
    phrases = tuple(p for p in (" ".join(_clean(m)) for m in _PHRASE.findall(q)) if p)
    rest = _PHRASE.sub(" ", q).replace('"', " ")
    tokens = _clean(rest)
    prefix: str | None = None
    if rest[-1:].isalnum():  # still typing a word
        pieces = _ALNUM_RUN.findall(tokens.pop())
        if len(pieces[-1]) >= MIN_PREFIX_CHARS:
            prefix = pieces.pop()
        tokens.extend(pieces)
    return ParsedQuery(phrases=phrases[:MAX_TERMS], terms=tuple(tokens[:MAX_TERMS]), prefix=prefix)


def tsquery_parts(p: ParsedQuery) -> list[tuple[str, str]]:
    """(sql_fn, arg) pairs the query builder ANDs with `&&`: one `phraseto_tsquery`
    (english) per phrase, one `plainto_tsquery` (english) for the terms, one `to_tsquery`
    (simple) for `<prefix>:*`. Only the parts that exist."""
    parts = [("phraseto_tsquery", phrase) for phrase in p.phrases]
    if p.terms:
        parts.append(("plainto_tsquery", " ".join(p.terms)))
    if p.prefix is not None:
        parts.append(("to_tsquery", f"{p.prefix}:*"))
    return parts


# ts_headline's default StartSel and StopSel around each match (the plan keeps them), and
# the mark for text a snippet leaves out (APP-F07).
START_SEL: Final = "<b>"
STOP_SEL: Final = "</b>"
ELLIPSIS: Final = "…"
_MARK: Final = re.compile(f"({re.escape(START_SEL)}|{re.escape(STOP_SEL)})")


def mark_cuts(headline: str, source: str) -> str:
    """`headline` (one ts_headline fragment of `source`) with ELLIPSIS before it when it
    starts after the start of `source`, and after it when it stops before the end.
    ts_headline returns the document's own text with the marks added, so the headline is a
    piece of `source` in which each mark may or may not be the document's own (a document
    can hold `<b>` itself; ts_headline does not escape it). When it is not a piece of
    `source`, the headline is returned as it came. With one fragment, ts_headline's
    FragmentDelimiter never shows, so this is the only sign of a cut (APP-F07)."""
    pieces = _MARK.split(headline)
    if not "".join(p for p in pieces if p not in (START_SEL, STOP_SEL)).strip():
        return headline
    pattern = "".join(
        f"(?:{re.escape(p)})?" if p in (START_SEL, STOP_SEL) else re.escape(p) for p in pieces
    )
    found = re.search(pattern, source)
    if found is None:
        return headline
    before = ELLIPSIS if source[: found.start()].strip() else ""
    after = ELLIPSIS if source[found.end() :].strip() else ""
    return f"{before}{headline}{after}"
