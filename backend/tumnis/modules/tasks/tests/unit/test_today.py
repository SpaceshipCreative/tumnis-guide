"""Today's list order (P0-18, FR-3.1): priority first (urgent to low), then the earliest
due date (undated last), then the oldest task."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest


@pytest.mark.req("FR-3.1")
@pytest.mark.wp("P0-18")
def test_today_order() -> None:
    """T-P0-18-19
    Priority, due, created: an urgent task beats a high one due earlier; among equal
    priorities the earlier due date wins and an undated task goes last; equal priority and
    due fall back to creation order.
    """
    from tumnis.modules.tasks.rules import TodayTask, today_order  # noqa: PLC0415

    t0 = datetime(2026, 3, 9, 8, tzinfo=UTC)

    def task(name: str, priority: str, due: date | None, minutes: int) -> TodayTask:
        return TodayTask(
            id=uuid.uuid5(uuid.NAMESPACE_URL, name),
            priority=priority,
            due_on=due,
            created_at=t0 + timedelta(minutes=minutes),
        )

    urgent = task("urgent", "urgent", None, 50)
    high_due_today = task("high today", "high", date(2026, 3, 9), 40)
    normal_due_today_old = task("normal today old", "normal", date(2026, 3, 9), 1)
    normal_due_today_new = task("normal today new", "normal", date(2026, 3, 9), 30)
    normal_due_later = task("normal later", "normal", date(2026, 3, 12), 0)
    normal_undated = task("normal undated", "normal", None, 0)
    low = task("low", "low", date(2026, 3, 1), 0)

    given = [
        low,
        normal_undated,
        normal_due_today_new,
        urgent,
        normal_due_later,
        high_due_today,
        normal_due_today_old,
    ]
    assert today_order(given) == [
        urgent,
        high_due_today,
        normal_due_today_old,
        normal_due_today_new,
        normal_due_later,
        normal_undated,
        low,
    ]
