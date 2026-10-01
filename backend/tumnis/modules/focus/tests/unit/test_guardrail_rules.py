"""The Guardrail rules (P4-01, FR-10.6): which task the one-task dashboard shows, which one it
prepares next, and what counts as a detour. Pure functions; the rules are imported inside
each test, so this file collects before they exist.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

A, B, C, D = (UUID(f"0199aa00-0000-7000-8000-00000000f1a{n}") for n in range(4))
OUTSIDE = UUID("0199aa00-0000-7000-8000-00000000f1af")


def _plan(*items: tuple[UUID, int, bool]) -> list[Any]:
    from tumnis.modules.focus.rules import PlanItemLite  # noqa: PLC0415

    return [PlanItemLite(task_id=t, position=p, accepted=ok) for t, p, ok in items]


def _tasks(**statuses: str) -> dict[UUID, Any]:
    from tumnis.modules.focus.rules import TaskLite  # noqa: PLC0415

    ids = {"a": A, "b": B, "c": C, "d": D, "outside": OUTSIDE}
    return {ids[name]: TaskLite(status=status) for name, status in statuses.items()}


ACCEPTED = ((A, 0, True), (B, 1, True), (C, 2, True))

# (plan items, task statuses, expected current, expected next after it)
TABLE: list[tuple[str, tuple[tuple[UUID, int, bool], ...], dict[str, str], Any, Any]] = [
    (
        "first accepted item when nothing is in progress",
        ACCEPTED,
        {"a": "today", "b": "today", "c": "today"},
        A,
        B,
    ),
    (
        "the In progress item wins over earlier ones",
        ACCEPTED,
        {"a": "today", "b": "in_progress", "c": "today"},
        B,
        C,
    ),
    (
        "Done and waiting on the human are skipped, both for current and next",
        ((A, 0, True), (B, 1, True), (C, 2, True), (D, 3, True)),
        {"a": "done", "b": "waiting_on_human", "c": "today", "d": "today"},
        C,
        D,
    ),
    (
        "next skips Done and waiting items after the current one",
        ((A, 0, True), (B, 1, True), (C, 2, True), (D, 3, True)),
        {"a": "in_progress", "b": "done", "c": "waiting_on_human", "d": "backlog"},
        A,
        D,
    ),
    (
        "order is by position, not by list order",
        ((C, 2, True), (A, 0, True), (B, 1, True)),
        {"a": "today", "b": "today", "c": "today"},
        A,
        B,
    ),
    (
        "items not accepted are not shown",
        ((A, 0, False), (B, 1, True), (C, 2, False)),
        {"a": "today", "b": "today", "c": "today"},
        B,
        None,
    ),
    (
        "an In progress task outside the plan (a detour) does not replace the plan's task",
        ACCEPTED,
        {"a": "today", "b": "today", "c": "today", "outside": "in_progress"},
        A,
        B,
    ),
    (
        "an item whose task is gone is skipped",
        ACCEPTED,
        {"b": "today", "c": "today"},
        B,
        C,
    ),
    (
        "nothing left: no current, no next",
        ACCEPTED,
        {"a": "done", "b": "done", "c": "waiting_on_human"},
        None,
        None,
    ),
    ("an empty plan", (), {}, None, None),
]


@pytest.mark.req("FR-10.6")
@pytest.mark.wp("P4-01")
@pytest.mark.xfail(strict=True, reason="spec:P4-01")
def test_current_and_next_task_table() -> None:
    """T-P4-01-02
    In progress (inside today's plan) wins; Done and waiting-on-the-human items are
    skipped; accepted items only, in position order; the next task follows the current
    one by the same rule, and there is none after the last or without a current one.
    """
    from tumnis.modules.focus.rules import (  # noqa: PLC0415
        current_guardrail_task,
        next_guardrail_task,
    )

    for case, items, statuses, current, after in TABLE:
        plan, tasks = _plan(*items), _tasks(**statuses)
        assert current_guardrail_task(plan, tasks) == current, case
        assert next_guardrail_task(plan, tasks, current) == after, case
    plan, tasks = _plan(*ACCEPTED), _tasks(a="today", b="today", c="today")
    assert next_guardrail_task(plan, tasks, None) is None
    assert next_guardrail_task(plan, tasks, C) is None
    assert next_guardrail_task(plan, tasks, OUTSIDE) is None  # not in the plan


@pytest.mark.req("FR-10.6")
@pytest.mark.wp("P4-01")
@pytest.mark.xfail(strict=True, reason="spec:P4-01")
def test_is_detour() -> None:
    """T-P4-01-03
    Free text (no task) and a task not in Today are detours; a task in Today is not.
    """
    from tumnis.modules.focus.rules import is_detour  # noqa: PLC0415

    today = frozenset({A, B})
    assert is_detour(None, today) is True
    assert is_detour(C, today) is True
    assert is_detour(A, today) is False
    assert is_detour(B, today) is False
    assert is_detour(None, frozenset()) is True
    assert is_detour(A, frozenset()) is True
