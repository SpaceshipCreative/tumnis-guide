"""The main worker's listen list (P1-16, ADR-0007): every queue register_queues registers,
except `extract`. DBOS.listen_queues takes names before launch while queues are registered
after it, so the two lists live apart; a queue added to one and not the other would never
be dequeued."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from dbos import DBOS

from tumnis import worker


@pytest.mark.req("ADR-0007")
@pytest.mark.wp("P1-16")
def test_main_worker_listens_to_every_queue_but_extract(monkeypatch: pytest.MonkeyPatch) -> None:
    registered: list[str] = []

    def record(name: str, **_: Any) -> None:
        registered.append(name)

    monkeypatch.setattr(DBOS, "register_queue", record)

    worker.register_queues()

    assert worker.EXTRACT_QUEUE in registered
    assert sorted(worker.main_queues()) == sorted(
        q for q in registered if q != worker.EXTRACT_QUEUE
    )


@pytest.mark.req("ADR-0007")
@pytest.mark.wp("P1-16")
def test_every_schedule_runs_on_a_main_worker_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """A schedule without `queue_name` goes to DBOS's internal queue, which every process
    services whatever `listen_queues` says (dbos 3.1.0 `_queue.py`), so the extract worker
    could run it. Each schedule names one of the main worker's queues instead."""
    applied: list[dict[str, Any]] = []
    monkeypatch.setattr(DBOS, "apply_schedules", applied.extend)

    worker.register_schedules(SimpleNamespace(deployment_env="prod"))  # type: ignore[arg-type]
    worker.register_audit_schedule()
    worker.register_module_schedules()
    worker.register_runner_sweep()
    worker.register_task_schedules()

    assert applied
    main = set(worker.main_queues())
    for schedule in applied:
        assert schedule.get("queue_name") in main, schedule["schedule_name"]
