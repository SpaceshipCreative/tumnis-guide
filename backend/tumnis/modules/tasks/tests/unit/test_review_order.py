"""The review queue's order (P1-13, FR-6.1, FR-11.4): blocking impact first (downstream
tasks plus human minutes, times Jev's factor when it applied), then age, then id.

The rules are read inside each test, so this file imports before they exist (the spec
tests are red, not a collection error). The Jev factor takes any answer with a `score` and
the route's value: tasks cannot import decisions (decisions calls tasks), so the test
stands in for decisions' ScoreAnswer and Route with the same shapes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

T0 = datetime(2026, 3, 9, 12, tzinfo=UTC)
TASKS = st.integers(min_value=0, max_value=50)
MINUTES = st.integers(min_value=0, max_value=2000)
FACTORS = st.sampled_from([0.5, 0.75, 1.0, 1.25, 1.5])


def _rules() -> Any:
    from tumnis.modules.tasks import rules  # noqa: PLC0415

    return rules


def _row(tasks: int, minutes: int, factor: float, created_at: datetime) -> Any:
    rules = _rules()
    return rules.ReviewRow(
        id=uuid.uuid4(),
        created_at=created_at,
        impact=rules.deterministic_impact(tasks, minutes),
        jev_factor=factor,
    )


@pytest.mark.req("FR-6.1")
@pytest.mark.wp("P1-13")
@given(a=st.tuples(TASKS, MINUTES), b=st.tuples(TASKS, MINUTES), factor=FACTORS)
def test_dominating_item_ranks_higher(
    a: tuple[int, int], b: tuple[int, int], factor: float
) -> None:
    """T-P1-13-01
    With equal Jev factors, an item with at least as many downstream tasks and minutes,
    and more of one, ranks before the other, even when it is the younger of the two.
    """
    high = (max(a[0], b[0]), max(a[1], b[1]))
    low = (min(a[0], b[0]), min(a[1], b[1]))
    if high == low:
        return  # neither dominates
    older = _row(*low, factor, T0)
    younger = _row(*high, factor, T0 + timedelta(hours=1))

    ordered = _rules().review_order([older, younger])

    assert ordered.index(younger) < ordered.index(older)


@pytest.mark.req("FR-6.1")
@pytest.mark.wp("P1-13")
@given(
    offsets=st.lists(st.integers(min_value=0, max_value=5), min_size=1, max_size=12),
    tasks=TASKS,
    minutes=MINUTES,
    factor=FACTORS,
    data=st.data(),
)
def test_ties_break_by_age(
    offsets: list[int], tasks: int, minutes: int, factor: float, data: st.DataObject
) -> None:
    """T-P1-13-02
    Equal impact: the older `created_at` first, then the id; the order is total (any
    permutation of the input gives the same list) and stable.
    """
    rows = [_row(tasks, minutes, factor, T0 + timedelta(minutes=n)) for n in offsets]
    shuffled = data.draw(st.permutations(rows))

    ordered = _rules().review_order(rows)

    assert ordered == sorted(rows, key=lambda row: (row.created_at, row.id))
    assert _rules().review_order(shuffled) == ordered
    assert _rules().review_order(ordered) == ordered


@dataclass(frozen=True)
class _Score:
    """decisions' ScoreAnswer, as far as the factor reads it."""

    score: float
    confidence: float = 0.9


@pytest.mark.req("FR-11.4")
@pytest.mark.wp("P1-13")
def test_jev_factor_only_when_applied() -> None:
    """T-P1-13-03
    Factor 1.0 for REVIEW, DETERMINISTIC, approval required or no answer; when applied,
    1 + 0.25 * (score - 2): 0.5 and 1.5 at the extreme levels, 1.0 in the middle.
    """
    jev_factor = _rules().jev_factor

    assert jev_factor(None, None) == 1.0
    assert jev_factor(None, "applied") == 1.0
    for route in ("review", "deterministic", "approval_required", None):
        assert jev_factor(_Score(4.0), route) == 1.0
        assert jev_factor(_Score(0.0), route) == 1.0
    assert jev_factor(_Score(0.0), "applied") == pytest.approx(0.5)
    assert jev_factor(_Score(4.0), "applied") == pytest.approx(1.5)
    assert jev_factor(_Score(2.0), "applied") == pytest.approx(1.0)
    assert jev_factor(_Score(3.0), "applied") == pytest.approx(1.25)
