"""`safe_rel_path` is the one gate every storage path passes (P1-14, SEC-5, FR-15.7): no
path can leave a location's root, whatever it is spelled with."""

from __future__ import annotations

import unicodedata

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tumnis.modules.knowledge.rules import PathRejected, safe_rel_path

HOSTILE_SEGMENTS = [
    "..",
    ".",
    "",
    "~",
    "C:",
    "\x00",
    "a\u2215b",
    "\uff0e\uff0e",
    "\u2025",
    "a\\b",
    "\u202e",
    "\x1f",
]
_segment = st.text(alphabet=st.characters(categories=["L", "N"]), min_size=1, max_size=12)


@st.composite
def hostile_paths(draw: st.DrawFn) -> str:
    segments = draw(st.lists(_segment, min_size=1, max_size=5))
    hostile = draw(
        st.lists(
            st.tuples(
                st.integers(min_value=0, max_value=len(segments) - 1),
                st.sampled_from(HOSTILE_SEGMENTS),
            ),
            min_size=1,
            max_size=len(segments),
        )
    )
    for index, bad in hostile:
        segments[index] = bad
    leading = draw(st.sampled_from(["", "/"]))
    return leading + "/".join(segments)


_safe_char = st.one_of(
    st.characters(categories=["L", "Nd"]),
    st.sampled_from([" ", "-"]),
)
_safe_segment = st.text(alphabet=_safe_char, min_size=1, max_size=20).filter(
    lambda s: s.strip() != ""
)


@pytest.mark.req("SEC-5")
@pytest.mark.wp("P1-14")
@pytest.mark.xfail(strict=True, reason="spec:P1-14")
@given(path=hostile_paths())
def test_hostile_paths_always_refused(path: str) -> None:
    """T-P1-14-04
    Any path with at least one hostile component ('..', '.', an empty segment, '~', a drive
    letter, NUL or another control character, a look-alike separator or dot, a bidi
    override), with or without a leading '/', raises PathRejected.
    """
    with pytest.raises(PathRejected):
        safe_rel_path(path)


@pytest.mark.req("FR-15.7")
@pytest.mark.wp("P1-14")
@pytest.mark.xfail(strict=True, reason="spec:P1-14")
@given(segments=st.lists(_safe_segment, min_size=1, max_size=5))
def test_safe_names_accepted_and_stable(segments: list[str]) -> None:
    """T-P1-14-05
    Paths made of letters (Latin or not), digits, spaces and dashes are accepted, come back
    NFC-normalized, and `safe_rel_path` is idempotent on its own answer.
    """
    path = "/".join(segments)
    safe = safe_rel_path(path)
    assert safe == unicodedata.normalize("NFC", path)
    assert safe_rel_path(safe) == safe


REFUSED = [
    "../x",
    "a/../../x",
    "/etc/passwd",
    "C:\\x",
    "a\\b",
    "a/\u2215b",
    "\uff0e\uff0e/x",
    "a//b",
    "a/",
    "a\x00b",
    "d/" + "x" * 256,
]
ACCEPTED = [
    ("notes/Plan 2026.md", "notes/Plan 2026.md"),
    ("uploads/Résumé.pdf", "uploads/Résumé.pdf"),
    (unicodedata.normalize("NFD", "uploads/Résumé.pdf"), "uploads/Résumé.pdf"),
]


@pytest.mark.req("SEC-5")
@pytest.mark.wp("P1-14")
@pytest.mark.xfail(strict=True, reason="spec:P1-14")
@pytest.mark.parametrize(
    "case",
    [pytest.param(("refused", p, None), id=f"refused-{i}") for i, p in enumerate(REFUSED)]
    + [
        pytest.param(("accepted", p, want), id=f"accepted-{i}")
        for i, (p, want) in enumerate(ACCEPTED)
    ],
)
def test_examples(case: tuple[str, str, str | None]) -> None:
    """T-P1-14-06
    `../x`, `a/../../x`, `/etc/passwd`, `C:\\x`, `a\\b`, `a/<division slash>b`,
    `<fullwidth dots>/x`, `a//b`, `a/`, `a<NUL>b` and a 256-byte segment are refused;
    `notes/Plan 2026.md` and `uploads/Résumé.pdf` (NFC or NFD) are accepted,
    NFC-normalized.
    """
    verdict, path, want = case
    if verdict == "refused":
        with pytest.raises(PathRejected):
            safe_rel_path(path)
    else:
        assert safe_rel_path(path) == want
