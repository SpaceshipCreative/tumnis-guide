"""search pure rules: no I/O, `now` and `tz` passed in (P0-20, FR-3.9).

Query parsing: what the user typed becomes phrases ("double quotes"), complete terms and
the prefix still being typed. Every tsquery operator is stripped first, and the prefix is
letters and digits only, so user text reaches Postgres only as the bound argument of one
of three fixed functions (`tsquery_parts`), never as tsquery syntax.

Ranking constants: `score = ts_rank_cd * (PROJECT_BOOST on a project match)
/ (1 + age_days / RECENCY_DAYS)`. Changing one is a spec-change PR that updates
backend/fixtures/search/corpus.yaml.
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
