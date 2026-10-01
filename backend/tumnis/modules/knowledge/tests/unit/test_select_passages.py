"""Choosing the knowledge part of a packet (P1-17, FR-15.4): the project brief first, then
ranked passages up to a character cap, each still citing its document and page."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

if TYPE_CHECKING:
    from tumnis.modules.knowledge.rules import Passage

BRIEF_DOC = uuid.UUID(int=1)
RATE_CARD = uuid.UUID(int=2)
TERMS = uuid.UUID(int=3)
MARKER = "[brief truncated]"


def _brief(text: str) -> Passage:
    from tumnis.modules.knowledge.rules import Passage  # noqa: PLC0415

    return Passage(
        chunk_id=None,
        document_id=BRIEF_DOC,
        title="Acme brief",
        heading_path=[],
        page=None,
        text=text,
    )


def _passage(n: int, text: str, *, document_id: uuid.UUID = RATE_CARD, page: int = 2) -> Passage:
    from tumnis.modules.knowledge.rules import Passage  # noqa: PLC0415

    return Passage(
        chunk_id=uuid.UUID(int=100 + n),
        document_id=document_id,
        title="Rate card",
        heading_path=["Acme rate card", "Rates"],
        page=page,
        text=text,
    )


def _case_brief_first() -> tuple[Any, list[Any], int, list[str]]:
    brief = _brief("Acme's marketing site rebuild.")
    ranked = [_passage(1, "Senior designer | 160"), _passage(2, "Developer | 140", page=3)]
    return brief, ranked, 6_000, [brief.text, "Senior designer | 160", "Developer | 140"]


def _case_brief_truncated() -> tuple[Any, list[Any], int, list[str]]:
    brief = _brief("b" * 5_000)
    ranked = [_passage(1, "Senior designer | 160")]
    cut = ("b" * 5_000)[: 3_000 - len(MARKER) - 1] + "\n" + MARKER
    return brief, ranked, 6_000, [cut, "Senior designer | 160"]


def _case_stops_at_first_overflow() -> tuple[Any, list[Any], int, list[str]]:
    brief = _brief("x" * 100)
    ranked = [_passage(1, "a" * 500), _passage(2, "b" * 600), _passage(3, "c" * 10)]
    return brief, ranked, 1_000, ["x" * 100, "a" * 500]


def _case_no_duplicates() -> tuple[Any, list[Any], int, list[str]]:
    brief = _brief("The brief.")
    ranked = [
        _passage(1, "The brief, as a chunk.", document_id=BRIEF_DOC),
        _passage(2, "Senior designer | 160"),
        _passage(3, "Senior designer | 160"),
        _passage(4, "Invoices are due within thirty days.", document_id=TERMS, page=3),
    ]
    return (
        brief,
        ranked,
        6_000,
        [
            "The brief.",
            "Senior designer | 160",
            "Invoices are due within thirty days.",
        ],
    )


def _case_no_brief() -> tuple[Any, list[Any], int, list[str]]:
    ranked = [_passage(1, "Senior designer | 160")]
    return None, ranked, 6_000, ["Senior designer | 160"]


CASES = {
    "brief_first": _case_brief_first,
    "brief_truncated": _case_brief_truncated,
    "stops_at_first_overflow": _case_stops_at_first_overflow,
    "no_duplicates": _case_no_duplicates,
    "no_brief": _case_no_brief,
}


@pytest.mark.req("FR-15.4")
@pytest.mark.wp("P1-17")
@pytest.mark.parametrize("case", list(CASES))
def test_brief_first_cap_and_stop(case: str) -> None:
    """T-P1-17-11
    The brief comes first; a brief longer than half the cap is cut to half the cap,
    ending in a `[brief truncated]` line; ranked passages follow in order, skipping the
    brief's own document and exact duplicates, and the list stops at the first passage
    that would take it over the cap (no partial passages, nothing after it). Every
    passage keeps its citation (chunk, document, title, heading path, page).
    """
    from tumnis.modules.knowledge.rules import select_passages  # noqa: PLC0415

    brief, ranked, cap, expected = CASES[case]()

    out = select_passages(brief, ranked, cap)

    assert [p.text for p in out] == expected
    assert sum(len(p.text) for p in out) <= cap
    if brief is not None:
        assert out[0].document_id == BRIEF_DOC
        assert out[0].chunk_id is None
    by_text: dict[str, Any] = {}
    for p in ranked:
        by_text.setdefault(p.text, p)  # the first of exact duplicates is the one kept
    for p in out[1 if brief is not None else 0 :]:
        source = by_text[p.text]
        assert (p.chunk_id, p.document_id, p.title, p.heading_path, p.page) == (
            source.chunk_id,
            source.document_id,
            source.title,
            source.heading_path,
            source.page,
        )


@pytest.mark.req("FR-15.4")
@pytest.mark.wp("P1-17")
@settings(max_examples=200, deadline=None)
@given(
    brief_len=st.one_of(st.none(), st.integers(0, 20_000)),
    ranked_lens=st.lists(st.integers(1, 3_000), max_size=30),
    cap=st.integers(500, 20_000),
)
def test_never_exceeds_cap(brief_len: int | None, ranked_lens: list[int], cap: int) -> None:
    """T-P1-17-12
    For any brief (none, or 0 to 20,000 characters), any ranked passages (1 to 3,000
    characters each) and any cap from 500 to 20,000, the chosen passages' text never adds
    up to more than the cap, and the brief is first whenever one is given.
    """
    from tumnis.modules.knowledge.rules import select_passages  # noqa: PLC0415

    brief = None if brief_len is None else _brief("b" * brief_len)
    ranked = [
        _passage(i, chr(ord("a") + i % 26) * n, document_id=uuid.UUID(int=1_000 + i))
        for i, n in enumerate(ranked_lens)
    ]

    out = select_passages(brief, ranked, cap)

    assert sum(len(p.text) for p in out) <= cap
    if brief is not None:
        assert out[0].document_id == BRIEF_DOC
