"""uuid7() for Python-side IDs (P0-06, ADR-0005)."""

import time
import uuid
from itertools import pairwise

import pytest

from tumnis.core import ids


@pytest.mark.req("ADR-0005")
@pytest.mark.wp("P0-06")
def test_uuid7_is_version_7_and_sorts_in_creation_order() -> None:
    """Version 7, RFC 9562 variant, and 10,000 IDs made in a row sort as they were made
    (the counter keeps order within one millisecond)."""
    before_ms = time.time_ns() // 1_000_000
    made = [ids.uuid7() for _ in range(10_000)]
    after_ms = time.time_ns() // 1_000_000

    assert {u.version for u in made} == {7}
    assert {u.variant for u in made} == {uuid.RFC_4122}
    assert all(a < b for a, b in pairwise(made))
    assert len(set(made)) == len(made)
    first_ms = made[0].int >> 80
    assert before_ms <= first_ms <= after_ms


@pytest.mark.req("ADR-0005")
@pytest.mark.wp("P0-06")
def test_uuid7_counter_overflow_borrows_the_next_millisecond(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the clock stuck, 4,096 IDs in one millisecond still come out increasing."""
    monkeypatch.setattr(time, "time_ns", lambda: 1_700_000_000_000_000_000)
    monkeypatch.setattr(ids, "_last_ms", -1)
    made = [ids._uuid7_fallback() for _ in range(5_000)]
    assert all(a < b for a, b in pairwise(made))
    assert made[-1].int >> 80 > made[0].int >> 80
