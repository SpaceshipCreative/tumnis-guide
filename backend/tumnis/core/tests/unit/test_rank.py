"""Fractional ranking keys (P0-17, FR-2.1): the shared vectors (read by Vitest too), strict
betweenness and long-run order under random moves."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

VECTORS = Path(__file__).resolve().parents[4] / "fixtures" / "rank" / "vectors.json"


def _vectors() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(VECTORS.read_text())
    return rows


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P0-17")
@pytest.mark.parametrize("row", _vectors(), ids=lambda row: f"{row['a']}-{row['b']}")
def test_vectors(row: dict[str, Any]) -> None:
    """T-P0-17-05
    Every row in `backend/fixtures/rank/vectors.json`: `between(a, b)` gives `out`, or
    raises `RankError` for an `error` row.
    """
    from tumnis.core.rank import RankError, between  # noqa: PLC0415

    if row.get("error"):
        with pytest.raises(RankError):
            between(row["a"], row["b"])
    else:
        assert between(row["a"], row["b"]) == row["out"]


def _fold(steps: list[tuple[int, bool]]) -> list[str]:
    """Valid keys built by folding random `between` calls from None: each step inserts
    before or after the key at a drawn index."""
    from tumnis.core.rank import between  # noqa: PLC0415

    keys = [between(None, None)]
    for index, after in steps:
        i = index % len(keys)
        if after:
            right = keys[i + 1] if i + 1 < len(keys) else None
            keys.insert(i + 1, between(keys[i], right))
        else:
            left = keys[i - 1] if i > 0 else None
            keys.insert(i, between(left, keys[i]))
    return keys


steps = st.lists(st.tuples(st.integers(min_value=0, max_value=10_000), st.booleans()), max_size=40)


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P0-17")
@given(built=steps, pick=st.integers(min_value=0, max_value=10_000))
def test_between_is_strictly_between(built: list[tuple[int, bool]], pick: int) -> None:
    """T-P0-17-06
    For sorted valid keys (built by folding random `between` calls from None), any
    adjacent pair (a, b) and the open ends: `a < between(a, b) < b` under Python `str`
    ordering, the result passes `validate` and never ends in "0".
    """
    from tumnis.core.rank import between, validate  # noqa: PLC0415

    keys = sorted(set(_fold(built)))
    bounded = [(None, keys[0]), (keys[-1], None)]
    if len(keys) > 1:
        i = pick % (len(keys) - 1)
        bounded.append((keys[i], keys[i + 1]))
    for a, b in bounded:
        key = between(a, b)
        validate(key)
        assert not key.endswith("0")
        assert a is None or a < key
        assert b is None or key < b


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P0-17")
@settings(max_examples=25, deadline=None)
@given(seed=st.integers(min_value=0, max_value=2**32))
def test_1000_random_moves_stay_ordered(seed: int) -> None:
    """T-P0-17-07
    Start with `n_keys(20)`; 1,000 random (remove index i, insert index j) moves, each
    insert keyed with `between(left, right)`. After every move the list is sorted, the
    keys are unique and none is longer than `MAX_KEY_LEN`.
    """
    from tumnis.core.rank import MAX_KEY_LEN, between, n_keys  # noqa: PLC0415

    rng = random.Random(seed)  # noqa: S311  # uniform draws; adversarial runs: T-P0-17-10
    keys = n_keys(20)
    assert keys == sorted(keys)
    for _ in range(1000):
        i, j = rng.randrange(20), rng.randrange(20)
        keys.pop(i)
        left = keys[j - 1] if 0 < j <= len(keys) else None
        right = keys[j] if j < len(keys) else None
        keys.insert(j, between(left, right))
        assert keys == sorted(keys)
        assert len(set(keys)) == len(keys)
        assert max(len(k) for k in keys) <= MAX_KEY_LEN
