"""The lifecycle state machine again, through `tasks.api.change_status` on Postgres (P0-18,
FR-3.2): after every step the row is read back as the owner. An accepted transition is a
matrix cell and raises the version; a refused one is not and leaves the row as it was; the
side-effect invariants hold on the stored rows.

The machine drives the async api on an event loop of its own (Hypothesis runs rules
synchronously); the engines keep no idle connections, so nothing outlives the loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    invariant,
    rule,
    run_state_machine_as_test,
)

from tumnis.modules.tasks.tests.conftest import owner_rows
from tumnis.modules.tasks.tests.unit.transition_matrix import ACTORS, EXPECTED, LABELS, STATUSES

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tumnis.core.clock import FixedClock
    from tumnis.modules.tasks.tests.conftest import MakeTask, SetStatus

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

COLUMNS = "status, label, version, rollover_count, started_at, completed_at, actual_minutes"


class _Row:
    def __init__(self, raw: tuple[Any, ...]) -> None:
        (
            self.status,
            self.label,
            self.version,
            self.rollover_count,
            self.started_at,
            self.completed_at,
            self.actual_minutes,
        ) = raw


def _machine(
    loop: asyncio.AbstractEventLoop,
    db: DbUrls,
    clock: FixedClock,
    make_task: MakeTask,
    set_status: SetStatus,
) -> type[RuleBasedStateMachine]:
    def run(coro: Awaitable[Any]) -> Any:
        return loop.run_until_complete(coro)

    def read(task_id: UUID) -> _Row:
        [raw] = owner_rows(db, f"SELECT {COLUMNS} FROM tasks WHERE id = %s", (task_id,))  # noqa: S608
        return _Row(raw)

    class TaskLifecyclePg(RuleBasedStateMachine):
        tasks = Bundle("tasks")

        def __init__(self) -> None:
            super().__init__()
            self.ids: list[UUID] = []
            self.max_rollover: dict[UUID, int] = {}

        @rule(target=tasks, label=st.sampled_from(LABELS))
        def create(self, label: str | None) -> UUID:
            estimate = None if label in {"ai", None} else 30
            task = run(make_task(label=label, estimate_minutes=estimate))
            self.ids.append(task.id)
            self.max_rollover[task.id] = 0
            row = read(task.id)
            assert (row.status, row.label, row.version) == ("backlog", label, task.version)
            task_id: UUID = task.id
            return task_id

        def _try(self, task_id: UUID, to: str, actor: str) -> None:
            before = read(task_id)
            allowed = EXPECTED[before.status, to, actor, before.label]
            try:
                run(set_status(SimpleNamespace(id=task_id, version=before.version), to, actor))
            except Exception as exc:
                if getattr(exc, "code", None) != "transition_not_allowed":
                    raise
                assert not allowed, (before.status, to, actor, before.label)
                after = read(task_id)
                assert after.version == before.version
                assert after.status == before.status
                return
            assert allowed, (before.status, to, actor, before.label)
            after = read(task_id)
            assert after.status == to
            assert after.version > before.version

        @rule(t=tasks, to=st.sampled_from(STATUSES), actor=st.sampled_from(ACTORS))
        def transition(self, t: UUID, to: str, actor: str) -> None:
            self._try(t, to, actor)

        @rule(t=tasks)
        def day_close(self, t: UUID) -> None:
            if read(t).status == "today":
                self._try(t, "backlog", "system")

        @rule(minutes=st.integers(min_value=1, max_value=600))
        def tick(self, minutes: int) -> None:
            clock.advance(timedelta(minutes=minutes))

        @invariant()
        def stored_rows_keep_the_invariants(self) -> None:
            for task_id in self.ids:
                row = read(task_id)
                assert row.status in STATUSES  # one status, a single enum column
                assert row.rollover_count >= self.max_rollover[task_id]
                self.max_rollover[task_id] = row.rollover_count
                assert (row.completed_at is not None) == (row.status == "done")
                if row.completed_at and row.started_at:
                    assert row.started_at <= row.completed_at
                if row.label == "ai":
                    assert row.actual_minutes is None

    return TaskLifecyclePg


class TestTaskLifecyclePg:
    @pytest.mark.req("FR-3.2")
    @pytest.mark.wp("P0-18")
    @pytest.mark.xfail(strict=True, reason="spec:P0-18")
    def test_random_lifecycles_match_the_matrix_on_postgres(
        self,
        db: DbUrls,
        clock: FixedClock,
        make_task: MakeTask,
        set_status: SetStatus,
    ) -> None:
        """T-P0-18-05
        The stateful machine of T-P0-18-04 through `api.change_status`: each row is read
        back after every step; the version strictly increases on every accepted transition
        and a rejected one leaves it unchanged (25 examples, plan default).
        """
        from tumnis.core import db as core_db  # noqa: PLC0415

        loop = asyncio.new_event_loop()
        try:
            machine = _machine(loop, db, clock, make_task, set_status)
            run_state_machine_as_test(  # type: ignore[no-untyped-call]
                machine,
                settings=settings(max_examples=25, stateful_step_count=30, deadline=None),
            )
        finally:
            loop.run_until_complete(core_db.dispose())
            loop.close()
