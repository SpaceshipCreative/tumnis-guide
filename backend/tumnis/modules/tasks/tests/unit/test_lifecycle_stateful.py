"""Hypothesis walks random task lifecycles through `apply_transition` (P0-18, FR-3.2): every
accepted step is a matrix cell, every refused one is not, and the side effects keep their
invariants (rollover count never falls, completion fields match the status, AI work never
records human minutes).

The strategies over the rules' enums are deferred, so this file imports before the rules
exist (the spec test is red, not a collection error).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import Bundle, RuleBasedStateMachine, invariant, rule

from tumnis.core.clock import FixedClock
from tumnis.modules.tasks.tests.unit.transition_matrix import EXPECTED


def _rules() -> Any:
    from tumnis.modules.tasks import rules  # noqa: PLC0415

    return rules


LABELS = st.deferred(lambda: st.sampled_from([*_rules().Label, None]))  # None = pending
STATUSES = st.deferred(lambda: st.sampled_from(list(_rules().Status)))
ACTORS = st.deferred(lambda: st.sampled_from(list(_rules().ActorKind)))


class TaskLifecycle(RuleBasedStateMachine):
    tasks = Bundle("tasks")

    def __init__(self) -> None:
        super().__init__()
        self.clock = FixedClock(datetime(2026, 3, 9, 12, tzinfo=UTC))
        self.state: dict[int, Any] = {}
        self.max_rollover: dict[int, int] = {}

    @rule(target=tasks, label=LABELS)
    def create(self, label: Any) -> int:
        r = _rules()
        tid = len(self.state)
        self.state[tid] = r.TaskState(r.Status.BACKLOG, label, 0, None, None, None)
        self.max_rollover[tid] = 0
        return tid

    @rule(t=tasks, to=STATUSES, actor=ACTORS)
    def transition(self, t: int, to: Any, actor: Any) -> None:
        r = _rules()
        before = self.state[t]
        allowed = EXPECTED.get((before.status, to, actor, before.label), False)
        try:
            after = r.apply_transition(before, to, actor, self.clock.now())
        except r.TransitionNotAllowed:
            assert not allowed
            return
        assert allowed
        assert after.status is to
        self.state[t] = after

    @rule(t=tasks)
    def day_close(self, t: int) -> None:
        r = _rules()
        if self.state[t].status is r.Status.TODAY:
            self.state[t] = r.apply_transition(
                self.state[t], r.Status.BACKLOG, r.ActorKind.SYSTEM, self.clock.now()
            )

    @rule(minutes=st.integers(min_value=1, max_value=600))
    def tick(self, minutes: int) -> None:
        self.clock.advance(timedelta(minutes=minutes))

    @invariant()
    def rollover_never_falls(self) -> None:
        for t, s in self.state.items():
            assert s.rollover_count >= self.max_rollover[t]
            self.max_rollover[t] = s.rollover_count

    @invariant()
    def completion_fields_match_status(self) -> None:
        r = _rules()
        for s in self.state.values():
            assert (s.completed_at is not None) == (s.status is r.Status.DONE)
            if s.completed_at and s.started_at:
                assert s.started_at <= s.completed_at

    @invariant()
    def actual_minutes_only_for_human_time(self) -> None:
        r = _rules()
        for s in self.state.values():
            if s.label is r.Label.AI:
                assert s.actual_minutes is None


TaskLifecycle.TestCase.settings = settings(max_examples=300, stateful_step_count=60, deadline=None)


@pytest.mark.req("FR-3.2")
@pytest.mark.wp("P0-18")
class TestTaskLifecycle(TaskLifecycle.TestCase):  # type: ignore[misc,valid-type]
    """T-P0-18-04
    Invariants over random actions: create, transition by any actor to any status, day
    close (today -> backlog by the system) and clock ticks.
    """
