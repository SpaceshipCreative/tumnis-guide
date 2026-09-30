"""Run rules (P2-04, SAF-5, FR-5.4, R-22, R-29): the run status transitions, active time
without the time spent waiting on a human, and who may be dispatched."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import product

import pytest

T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


def _at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


@pytest.mark.req("SAF-5")
@pytest.mark.wp("P2-04")
@pytest.mark.xfail(strict=True, reason="spec:P2-04")
def test_active_seconds_excludes_waiting() -> None:
    """T-P2-04-03
    The 60-minute cap counts active time only: on a fixed clock, 20 minutes running, 45
    minutes waiting on a human, then 40 minutes running is 60 minutes of active time (the
    cap is reached) although 105 minutes passed on the wall clock; the ceiling is judged on
    the wall clock.
    """
    from tumnis.modules.agents.rules import active_seconds, over_ceiling  # noqa: PLC0415

    spans = [
        (_at(0), _at(20), False),
        (_at(20), _at(65), True),
        (_at(65), _at(105), False),
    ]
    assert active_seconds(spans) == 60 * 60
    assert active_seconds(spans[:2]) == 20 * 60
    assert active_seconds([(_at(0), _at(30), True)]) == 0
    assert active_seconds([]) == 0
    assert active_seconds(spans) >= 60 * 60  # the cap
    assert active_seconds(spans[:1] + spans[2:]) == 60 * 60

    ceiling = timedelta(hours=24)
    assert not over_ceiling(T0, T0 + timedelta(hours=23, minutes=59), ceiling)
    assert over_ceiling(T0, T0 + timedelta(hours=24), ceiling)


@pytest.mark.req("FR-5.4")
@pytest.mark.wp("P2-04")
@pytest.mark.xfail(strict=True, reason="spec:P2-04")
def test_can_dispatch_stuck_any_label() -> None:
    """T-P2-04-19
    `stuck` may be dispatched for a Human, AI or Hybrid task (R-23); `task` only for AI and
    Hybrid. Only backlog, today and in-progress tasks run; a project without a ready
    profile, or a task with an active run of the same kind, is refused.
    """
    from tumnis.modules.agents.rules import (  # noqa: PLC0415
        DispatchProfile,
        DispatchTask,
        RunKind,
        can_dispatch,
    )

    ready = DispatchProfile(status="ready")
    for label in ("human", "ai", "hybrid"):
        task = DispatchTask(label=label, status="today", active_kinds=frozenset())
        assert can_dispatch(task, RunKind.STUCK, ready) is None

    cases: list[tuple[str | None, bool]] = [
        ("ai", True),
        ("hybrid", True),
        ("human", False),
        (None, False),
    ]
    for label, allowed in cases:
        task = DispatchTask(label=label, status="backlog", active_kinds=frozenset())
        refusal = can_dispatch(task, RunKind.TASK, ready)
        assert (refusal is None) is allowed, label
        if not allowed:
            assert refusal is not None
            assert refusal.code == "label_not_runnable"

    for status in ("backlog", "today", "in_progress"):
        task = DispatchTask(label="ai", status=status, active_kinds=frozenset())
        assert can_dispatch(task, RunKind.TASK, ready) is None
    for status in ("waiting_on_human", "in_review", "done"):
        task = DispatchTask(label="ai", status=status, active_kinds=frozenset())
        refusal = can_dispatch(task, RunKind.TASK, ready)
        assert refusal is not None
        assert refusal.code == "status_not_runnable"

    task = DispatchTask(label="ai", status="today", active_kinds=frozenset())
    for profile in (None, DispatchProfile(status="provisioning"), DispatchProfile(status="paused")):
        refusal = can_dispatch(task, RunKind.TASK, profile)
        assert refusal is not None
        assert refusal.code == "no_ready_profile"

    busy = DispatchTask(label="ai", status="in_progress", active_kinds=frozenset({RunKind.TASK}))
    refusal = can_dispatch(busy, RunKind.TASK, ready)
    assert refusal is not None
    assert refusal.code == "run_already_active"
    assert can_dispatch(busy, RunKind.STUCK, ready) is None


# The table the plan defines (P2-04 Interfaces); every other pair is refused.
EXPECTED = {
    "queued": {"running", "held", "cancelled", "failed"},
    "held": {"running", "cancelled"},
    "running": {"waiting_on_human", "succeeded", "failed", "cancelled", "timed_out", "runner_lost"},
    "waiting_on_human": {"running", "failed", "cancelled", "timed_out", "runner_lost"},
}


@pytest.mark.req("FR-5.4")
@pytest.mark.wp("P2-04")
@pytest.mark.xfail(strict=True, reason="spec:P2-04")
def test_run_transition_table() -> None:
    """T-P2-04-17
    Every (current, target) pair of run statuses is allowed or refused exactly as
    `RUN_TRANSITIONS` says; a terminal status goes nowhere.
    """
    from tumnis.modules.agents.rules import (  # noqa: PLC0415
        RUN_TRANSITIONS,
        TERMINAL_STATUSES,
        RunStatus,
        TransitionNotAllowed,
        run_transition,
    )

    assert {k.value: {v.value for v in vs} for k, vs in RUN_TRANSITIONS.items()} == EXPECTED
    for current, target in product(RunStatus, RunStatus):
        allowed = target.value in EXPECTED.get(current.value, set())
        if allowed:
            assert run_transition(current, target) is target
        else:
            with pytest.raises(TransitionNotAllowed):
                run_transition(current, target)
    for terminal in TERMINAL_STATUSES:
        assert terminal not in RUN_TRANSITIONS
