"""Search query parsing (P0-20, FR-3.9): phrases, complete terms and the prefix being typed,
with every tsquery operator stripped, so user text only ever reaches Postgres as a bound
argument of a fixed function."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

# Characters that carry meaning in `to_tsquery` syntax (plan: parse_query strips them).
TSQUERY_SYNTAX = frozenset("&|!():*<>'\\")


@pytest.mark.req("FR-3.9")
@pytest.mark.wp("P0-20")
def test_parse_cases() -> None:
    """T-P0-20-01
    `'"send invoice" acme inv'` -> phrases ("send invoice",), terms ("acme",), prefix
    "inv"; `'acme '` (trailing space) -> terms ("acme",), no prefix; `'a & b | !c'` ->
    terms ("a", "b", "c"); `''` -> empty. The parts for the first are one phrase, the terms
    and the prefix with `:*`, in that order; an empty query has no parts.
    """
    from tumnis.modules.search.rules import ParsedQuery, parse_query, tsquery_parts  # noqa: PLC0415

    full = parse_query('"send invoice" acme inv')
    assert full == ParsedQuery(phrases=("send invoice",), terms=("acme",), prefix="inv")
    assert tsquery_parts(full) == [
        ("phraseto_tsquery", "send invoice"),
        ("plainto_tsquery", "acme"),
        ("to_tsquery", "inv:*"),
    ]

    assert parse_query("acme ") == ParsedQuery(phrases=(), terms=("acme",), prefix=None)
    assert parse_query("a & b | !c").terms == ("a", "b", "c")
    assert parse_query("a & b | !c").prefix is None

    empty = parse_query("")
    assert empty == ParsedQuery(phrases=(), terms=(), prefix=None)
    assert tsquery_parts(empty) == []


@pytest.mark.req("FR-3.9")
@pytest.mark.wp("P0-20")
@given(q=st.text(max_size=200))
def test_parse_never_emits_tsquery_syntax(q: str) -> None:
    """T-P0-20-02
    For any unicode input: no phrase, term or prefix holds a tsquery operator; the prefix
    is letters and digits only (so `<prefix>:*` is one lexeme); at most MAX_TERMS terms;
    every part is one of the three fixed functions, and only the prefix part ends in `:*`.
    """
    from tumnis.modules.search.rules import MAX_TERMS, parse_query, tsquery_parts  # noqa: PLC0415

    parsed = parse_query(q)
    for text in (*parsed.phrases, *parsed.terms):
        assert text
        assert not set(text) & TSQUERY_SYNTAX
    assert len(parsed.terms) <= MAX_TERMS
    if parsed.prefix is not None:
        assert parsed.prefix
        assert all(ch.isalnum() for ch in parsed.prefix)
    for fn, arg in tsquery_parts(parsed):
        assert fn in {"phraseto_tsquery", "plainto_tsquery", "to_tsquery"}
        if fn == "to_tsquery":
            assert arg == f"{parsed.prefix}:*"
        else:
            assert not set(arg) & TSQUERY_SYNTAX
