"""Edges of the ranking keys beyond the shared vectors (P0-17, FR-2.1): malformed keys,
integer parts changing length, and the smallest and largest integers."""

from __future__ import annotations

import pytest

from tumnis.core.rank import SMALLEST_INTEGER, RankError, between, n_keys, validate

LARGEST_INTEGER = "z" * 27


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P0-17")
@pytest.mark.parametrize("key", ["", "b1", "a!", SMALLEST_INTEGER, "a0V0"])
def test_malformed_keys_are_refused(key: str) -> None:
    """Empty, too short for its head, a non-digit, the smallest integer itself, and a
    trailing zero in the fraction are all refused."""
    with pytest.raises(RankError):
        validate(key)
    with pytest.raises(RankError):
        between(key, None)


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P0-17")
def test_integer_parts_grow_and_shrink_across_heads() -> None:
    """Counting up from `Xzzz` drops a digit (`Y00`); counting down from `b00` drops one
    too (`az`)."""
    assert between("Xzzz", None) == "Y00"
    assert between(None, "b00") == "az"


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P0-17")
def test_smallest_and_largest_integers_fall_back_to_fractions() -> None:
    """Below the smallest integer only fractions remain; above the largest the key grows a
    fraction instead of a new integer."""
    low = SMALLEST_INTEGER + "V"
    below = between(None, low)
    assert below.startswith(SMALLEST_INTEGER)
    assert below < low
    validate(below)
    above = between(LARGEST_INTEGER, None)
    assert above == LARGEST_INTEGER + "V"
    assert between(None, "A" + "0" * 25 + "1") == SMALLEST_INTEGER + "V"


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P0-17")
def test_n_keys_counts_up_from_the_start() -> None:
    assert n_keys(0) == []
    assert n_keys(3) == ["a0", "a1", "a2"]
