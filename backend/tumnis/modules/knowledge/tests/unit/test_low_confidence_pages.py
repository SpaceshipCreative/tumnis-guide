"""Which pages go to the vision model (P1-16, FR-15.2): Docling grades each page; a POOR
mean or low grade, or a page with no text items at all, is read again by the VLM."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from tumnis.modules.knowledge.rules import chunk_pages, low_confidence_pages

Grades = Mapping[int, str | tuple[str, str]]

CASES: list[tuple[str, Grades, Mapping[int, int], list[int]]] = [
    ("all_good", {1: "good", 2: "excellent"}, {1: 4, 2: 9}, []),
    ("poor_selects", {1: "good", 2: "poor"}, {1: 4, 2: 3}, [2]),
    ("poor_any_case", {1: "POOR"}, {1: 2}, [1]),
    ("fair_alone_does_not", {1: "fair", 2: "fair"}, {1: 1, 2: 5}, []),
    ("poor_low_grade", {1: ("fair", "poor")}, {1: 6}, [1]),
    ("poor_mean_grade", {1: ("poor", "fair")}, {1: 6}, [1]),
    ("fair_mean_and_low", {1: ("fair", "fair")}, {1: 6}, []),
    ("no_text_items", {1: "good", 2: "good"}, {1: 5, 2: 0}, [2]),
    ("text_items_missing_page", {1: "good", 3: "good"}, {1: 5}, [3]),
    ("ungraded_page_without_text", {}, {1: 0, 2: 7}, [1]),
    ("unspecified_with_text", {1: "unspecified"}, {1: 3}, []),
    ("sorted_and_once", {4: "poor", 2: "poor"}, {4: 0, 2: 1, 1: 3}, [2, 4]),
]


@pytest.mark.req("FR-15.2")
@pytest.mark.wp("P1-16")
@pytest.mark.parametrize(
    ("grades", "text_items", "expected"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_rule(grades: Grades, text_items: Mapping[int, int], expected: list[int]) -> None:
    """T-P1-16-09
    A page is selected when its Docling mean or low grade is POOR (any case; a grade is one
    value or a (mean, low) pair), or when it has no text items (a page missing from
    `text_items` has none); FAIR alone does not select it. Pages come back sorted, once.
    """
    assert low_confidence_pages(grades, text_items) == expected


@pytest.mark.req("FR-15.2")
@pytest.mark.wp("P1-16")
@pytest.mark.parametrize(
    ("pages", "expected"),
    [([], (None, None)), ([2], (2, 2)), ([3, 1, 2, 3], (1, 3))],
    ids=["none", "one", "spread"],
)
def test_chunk_pages(pages: list[int], expected: tuple[int | None, int | None]) -> None:
    """T-P1-16-09
    A chunk's page range is the lowest and highest page its items came from; a chunk with
    no page provenance (DOCX, Markdown, CSV) has none.
    """
    assert chunk_pages(pages) == expected
