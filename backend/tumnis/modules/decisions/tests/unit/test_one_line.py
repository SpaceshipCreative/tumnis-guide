"""`one_line`, the Generation slot's output cleanup (P1-03): a pure rule."""

from __future__ import annotations

import pytest

from tumnis.modules.decisions.rules import one_line


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Draft the outline.", "Draft the outline."),
        ("1. Draft the outline", "Draft the outline"),
        ("2) Draft the outline", "Draft the outline"),
        ("- **Draft the outline**", "Draft the outline"),
        ('"Draft the outline."', "Draft the outline."),
        ("“Draft the outline.”", "Draft the outline."),
        ("---\n\nDraft the outline.\nThen send it.", "Draft the outline."),
        ("Draft the outline. Then send it to Dana.", "Draft the outline."),
        ("3D print the bracket", "3D print the bracket"),
    ],
)
def test_one_line_keeps_the_first_sentence_without_markers(text: str, expected: str) -> None:
    """The first line with a letter or digit, without a list marker, wrapping quotes or
    emphasis, cut to its first sentence."""
    assert one_line(text, 120) == expected


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
def test_one_line_cuts_at_a_word_boundary() -> None:
    """Over the limit, the line is cut at the last space inside it and loses a trailing
    comma or dash; one word longer than the limit is cut inside the word."""
    assert one_line("Draft the outline, then the budget", 20) == "Draft the outline"
    assert one_line("Draft the outline - then the budget", 19) == "Draft the outline"
    assert one_line("x" * 30, 10) == "x" * 10


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
@pytest.mark.parametrize("text", ["", " \n\t", "...", "- \n* \n> ", '""'])
def test_one_line_without_words_is_none(text: str) -> None:
    """Nothing but whitespace or punctuation leaves nothing to show."""
    assert one_line(text, 120) is None
