"""The counter map: which events raise which counters, by how much, on which day (P0-21)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta, timezone

import pytest


@pytest.mark.req("Hosted readiness")
@pytest.mark.wp("P0-21")
@pytest.mark.xfail(strict=True, reason="spec:P0-21")
def test_known_events_map_to_counters() -> None:
    """T-P0-21-01
    `task.created` raises `tasks_created` by 1 and `project.created` raises
    `projects_created` by 1, whatever the payload holds; both are keys of `COUNTERS`. The
    usage day is the UTC date of `occurred_at`, also for an offset timestamp.
    """
    from tumnis.modules.usage.rules import COUNTERS, increments, usage_day  # noqa: PLC0415

    task = {
        "task_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "label": None,
        "source": "user",
        "tainted": False,
    }
    assert increments("task.created", task) == [("tasks_created", 1)]
    assert increments("project.created", {"project_id": str(uuid.uuid4())}) == [
        ("projects_created", 1)
    ]
    assert {"task.created", "project.created"} <= set(COUNTERS)
    assert [(name, fn({})) for name, fn in COUNTERS["task.created"]] == [("tasks_created", 1)]
    assert all(counters for counters in COUNTERS.values())

    assert usage_day(datetime(2026, 3, 8, 23, 30, tzinfo=UTC)) == date(2026, 3, 8)
    assert usage_day(datetime(2026, 3, 9, 0, 30, tzinfo=UTC)) == date(2026, 3, 9)
    new_york = timezone(timedelta(hours=-5))
    assert usage_day(datetime(2026, 3, 8, 20, 30, tzinfo=new_york)) == date(2026, 3, 9)


@pytest.mark.req("Hosted readiness")
@pytest.mark.wp("P0-21")
@pytest.mark.xfail(strict=True, reason="spec:P0-21")
def test_unknown_event_counts_nothing() -> None:
    """T-P0-21-02
    An event with no line in the map (and one with an empty payload) yields no increments
    and raises nothing.
    """
    from tumnis.modules.usage.rules import COUNTERS, increments  # noqa: PLC0415

    assert "test.ping" not in COUNTERS
    assert increments("test.ping", {"note": "x"}) == []
    assert increments("", {}) == []
    assert increments("task.status_changed", {"task_id": str(uuid.uuid4())}) == []
