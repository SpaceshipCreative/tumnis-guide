"""The runner sweep has its own queue (P1-04, FR-5.9): a long nightly or hourly job on the
single-slot maintenance queue must not hold back dead-runner detection."""

from __future__ import annotations

from typing import Any

import pytest
from dbos import DBOS

from tumnis import worker
from tumnis.core import workflows_ops


@pytest.mark.req("FR-5.9")
@pytest.mark.wp("P1-04")
def test_runner_sweep_is_scheduled_on_its_own_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sweep's schedule names the agents sweep queue, not the maintenance queue that
    audit_verify, housekeeping and the backup check share at concurrency 1."""
    applied: list[dict[str, Any]] = []
    monkeypatch.setattr(DBOS, "apply_schedules", applied.extend)

    worker.register_runner_sweep()

    (sweep,) = applied
    assert sweep["queue_name"] == worker._agents().RUNNER_SWEEP_QUEUE
    assert sweep["queue_name"] != workflows_ops.MAINTENANCE_QUEUE


@pytest.mark.req("FR-5.9")
@pytest.mark.wp("P1-04")
def test_the_sweep_queue_is_registered_one_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """register_queues registers the sweep queue with one worker slot, so two minutes'
    sweeps never overlap."""
    queues: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(DBOS, "register_queue", lambda name, **kw: queues.setdefault(name, kw))

    worker.register_queues()

    assert queues[worker._agents().RUNNER_SWEEP_QUEUE] == {"worker_concurrency": 1}
