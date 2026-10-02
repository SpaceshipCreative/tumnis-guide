"""APP-F07 (final application test): search snippets were cut mid-phrase with no ellipsis.

`ts_headline(..., 'MaxFragments=1,MaxWords=12,MinWords=4')` returns one fragment of the
document's own text, with each match between `<b>` and `</b>` (its default StartSel and
StopSel). Its FragmentDelimiter only separates several fragments, so one fragment cut from
the middle of a body ("Outline the three <b>proposal</b>") gave no sign it was cut. The
snippet now carries "…" where text was left out before or after the fragment. Every
headline below is what Postgres 18 returned for its document."""

from __future__ import annotations

import importlib

import pytest

pytestmark = [pytest.mark.req("FR-3.9"), pytest.mark.wp("P0-20")]

BODY = (
    "one two three four five six seven eight nine ten eleven twelve thirteen fourteen"
    " fifteen sixteen seventeen eighteen proposal nineteen twenty twentyone twentytwo"
    " twentythree twentyfour twentyfive"
)


@pytest.mark.parametrize(
    ("headline", "source", "expected"),
    [
        pytest.param(
            "First action: Outline the three <b>proposal</b> sections and then\nsend them",
            "First action: Outline the three proposal sections and then\nsend them to Acme"
            " for review by Friday please",
            "First action: Outline the three <b>proposal</b> sections and then\nsend them…",
            id="cut-at-the-end",
        ),
        pytest.param(
            "<b>proposal</b> nineteen twenty twentyone twentytwo twentythree twentyfour twentyfive",
            BODY,
            "…<b>proposal</b> nineteen twenty twentyone twentytwo twentythree twentyfour"
            " twentyfive",
            id="cut-at-the-start",
        ),
        pytest.param(
            "fourteen fifteen sixteen seventeen eighteen <b>proposal</b> nineteen twenty"
            " twentyone twentytwo twentythree twentyfour",
            BODY,
            "…fourteen fifteen sixteen seventeen eighteen <b>proposal</b> nineteen twenty"
            " twentyone twentytwo twentythree twentyfour…",
            id="cut-at-both-ends",
        ),
        pytest.param(
            "Open the <b>footer</b> component.\nThe <b>footer</b> link opens the right page",
            "Open the footer component.\nThe footer link opens the right page",
            "Open the <b>footer</b> component.\nThe <b>footer</b> link opens the right page",
            id="the-whole-document",
        ),
        pytest.param(
            "alpha beta gamma delta",
            "alpha beta gamma delta epsilon zeta eta theta",
            "alpha beta gamma delta…",
            id="no-match-shows-the-first-words",
        ),
        pytest.param(
            "<b>footer</b> link",
            "Fix footer link",
            "…<b>footer</b> link",
            id="a-short-first-word-dropped",
        ),
        pytest.param(
            "the <b>tag</b> <b> stays",
            "Write about the tag <b> stays put",
            "…the <b>tag</b> <b> stays…",
            id="source-with-literal-markup-still-shows-its-cuts",
            marks=pytest.mark.xfail(strict=True, reason="spec:FIX-app-final-minor"),
        ),
        pytest.param(
            "Write about the <b>tag</b>",
            "Write about the <b>tag</b>",
            "Write about the <b>tag</b>",
            id="a-literal-tag-mistaken-for-a-mark-still-is-found",
        ),
        pytest.param(
            "a <b>headline</b> from elsewhere",
            "a document that holds none of it",
            "a <b>headline</b> from elsewhere",
            id="not-in-the-document-is-left-as-it-came",
        ),
        pytest.param("", "", "", id="empty"),
    ],
)
def test_app_f07_snippet_marks_where_the_text_was_cut(
    headline: str, source: str, expected: str
) -> None:
    """A snippet starts with "…" when the fragment begins after the document's start and
    ends with "…" when it stops before the document's end, also when the document holds
    tags like the match marks (CodeRabbit on #183); it is left as it came when the
    fragment cannot be found in the document."""
    mark_cuts = importlib.import_module("tumnis.modules.search.rules").mark_cuts

    assert mark_cuts(headline, source) == expected
