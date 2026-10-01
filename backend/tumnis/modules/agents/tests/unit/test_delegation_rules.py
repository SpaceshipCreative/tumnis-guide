"""Delegation rules (P2-06, SAF-5, design decisions 3 and 6): the depth from a task's
delegation chain, loop detection, and how a run's status maps to `wait_for_task`'s answer.
Pure: time is an argument."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

NOW = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


def _record(task_id: uuid.UUID, minutes_ago: float, *, accepted: bool = False) -> Any:
    from tumnis.modules.agents.rules import (  # type: ignore[attr-defined]  # noqa: PLC0415
        DelegationRecord,
    )

    return DelegationRecord(
        delegation_id=uuid.uuid4(),
        task_id=task_id,
        delegated_at=NOW - timedelta(minutes=minutes_ago),
        accepted=accepted,
    )


@pytest.mark.req("SAF-5")
@pytest.mark.wp("P2-06")
@pytest.mark.xfail(strict=True, reason="spec:P2-06")
def test_depth_over_two_refused() -> None:
    """T-P2-06-06
    The new delegation's depth is the task's chain plus one: a root task delegates at depth
    1, a task one delegation down at 2 (allowed, the maximum), and a task whose chain holds
    two delegations would be depth 3, which is refused."""
    from tumnis.modules.agents.rules import (  # type: ignore[attr-defined]  # noqa: PLC0415
        MAX_DELEGATION_DEPTH,
        delegation_depth,
        depth_exceeded,
    )

    assert MAX_DELEGATION_DEPTH == 2
    one, two = _record(uuid.uuid4(), 5), _record(uuid.uuid4(), 10)
    assert delegation_depth([]) == 1
    assert delegation_depth([one]) == 2
    assert delegation_depth([one, two]) == 3
    assert not depth_exceeded(delegation_depth([]))
    assert not depth_exceeded(delegation_depth([one]))
    assert depth_exceeded(delegation_depth([one, two]))


@pytest.mark.req("SAF-5")
@pytest.mark.wp("P2-06")
@pytest.mark.xfail(strict=True, reason="spec:P2-06")
def test_loop_detection() -> None:
    """T-P2-06-08
    The third delegation of a task inside the window (two before it, no accepted result in
    between) is a loop; one before it is not; a delegation exactly LOOP_WINDOW old has left
    the window; an accepted result resets the count; other tasks' delegations do not count;
    and a task already in its own chain is a cycle."""
    from tumnis.modules.agents.rules import (  # type: ignore[attr-defined]  # noqa: PLC0415
        LOOP_REPEAT_LIMIT,
        LOOP_WINDOW,
        is_delegation_loop,
    )

    assert LOOP_REPEAT_LIMIT == 3
    assert timedelta(minutes=60) == LOOP_WINDOW
    task, other = uuid.uuid4(), uuid.uuid4()
    window_min = LOOP_WINDOW.total_seconds() / 60

    # Repeat limit.
    assert not is_delegation_loop([], task, NOW)
    assert not is_delegation_loop([_record(task, 5)], task, NOW)
    assert is_delegation_loop([_record(task, 20), _record(task, 5)], task, NOW)

    # Window edge: exactly LOOP_WINDOW ago is out; a second inside it is in.
    assert not is_delegation_loop([_record(task, window_min), _record(task, 5)], task, NOW)
    assert is_delegation_loop([_record(task, window_min - 1 / 60), _record(task, 5)], task, NOW)

    # An accepted result resets the count.
    assert not is_delegation_loop([_record(task, 30, accepted=True), _record(task, 5)], task, NOW)
    assert not is_delegation_loop(
        [_record(task, 40), _record(task, 30, accepted=True), _record(task, 5)], task, NOW
    )
    assert is_delegation_loop(
        [_record(task, 40, accepted=True), _record(task, 30), _record(task, 5)], task, NOW
    )

    # Other tasks' delegations do not count.
    assert not is_delegation_loop([_record(other, 20), _record(other, 5)], task, NOW)
    assert not is_delegation_loop([_record(other, 20), _record(task, 5)], task, NOW)

    # A cycle: the task is already in the chain that produced it.
    assert is_delegation_loop([], task, NOW, chain=[_record(other, 10), _record(task, 20)])
    assert not is_delegation_loop([], task, NOW, chain=[_record(other, 10)])


@pytest.mark.req("Design decision 6")
@pytest.mark.wp("P2-06")
@pytest.mark.xfail(strict=True, reason="spec:P2-06")
def test_wait_status_mapping() -> None:
    """T-P2-06-10
    `waiting_on_human` maps to `waiting_on_human`; every terminal status (succeeded,
    failed, cancelled, timed_out, runner_lost) to `done`; queued, held and running to
    `still_running`."""
    from tumnis.modules.agents.rules import (  # type: ignore[attr-defined]  # noqa: PLC0415
        RunStatus,
        wait_status,
    )

    expected = {
        RunStatus.QUEUED: "still_running",
        RunStatus.HELD: "still_running",
        RunStatus.RUNNING: "still_running",
        RunStatus.WAITING_ON_HUMAN: "waiting_on_human",
        RunStatus.SUCCEEDED: "done",
        RunStatus.FAILED: "done",
        RunStatus.CANCELLED: "done",
        RunStatus.TIMED_OUT: "done",
        RunStatus.RUNNER_LOST: "done",
    }
    assert set(expected) == set(RunStatus)
    for status, answer in expected.items():
        assert wait_status(status) == answer, status
