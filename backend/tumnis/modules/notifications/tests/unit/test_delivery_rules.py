"""The delivery rules (P2-16, FR-8.1, FR-8.4, UX 6): when a notification goes out on each
channel, and when what Quiet held is released. Pure functions; no clock, no network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import product
from uuid import UUID

import pytest

from tumnis.modules.notifications import rules

NOW = datetime(2026, 3, 9, 15, 0, tzinfo=UTC)
DAY_END = datetime(2026, 3, 9, 22, 0, tzinfo=UTC)
LEVELS: tuple[rules.Level, ...] = ("quiet", "nudge", "coach", "guardrail")
# Review-type items (R-05's kinds that need the person) and focus events.
KINDS = (
    "question",
    "approval",
    "result",
    "proposal",
    "focus.block_start",
    "focus.check_in_due",
    "focus.switched",
    "focus.day_end",
)


@pytest.mark.req("FR-8.4", "FR-8.1")
@pytest.mark.wp("P2-16")
def test_quiet_batches_while_in_progress() -> None:
    """T-P2-16-03
    The rule table: at Quiet with a task In progress every kind is batched; at every other
    level, and at Quiet with nothing In progress, every kind goes now. Whatever is decided,
    the in-app channel (the review badge and the focus bar) has it at once; Discord and
    browser push go exactly when the decision is "now". What Quiet held is flushed at the
    next natural break: once no task is In progress, or once the day's working hours end;
    an empty batch never flushes.
    """
    for level, busy, kind in product(LEVELS, (True, False), KINDS):
        decision = rules.delivery_decision(level, busy, kind)
        expected = "batch" if level == "quiet" and busy else "now"
        assert decision == expected, (level, busy, kind)
        channels = rules.channels_now(decision)
        assert "in_app" in channels, (level, busy, kind)
        assert ("discord" in channels) is (expected == "now"), (level, busy, kind)
        assert ("push" in channels) is (expected == "now"), (level, busy, kind)
        assert set(channels) <= {"in_app", "push", "discord"}

    held = [rules.NotificationView(UUID(int=1), "question", NOW - timedelta(minutes=5))]
    assert rules.flush_due(held, any_task_in_progress=True, now=NOW, day_end_at=DAY_END) is False
    assert rules.flush_due(held, any_task_in_progress=False, now=NOW, day_end_at=DAY_END) is True
    assert rules.flush_due(held, any_task_in_progress=True, now=DAY_END, day_end_at=DAY_END)
    assert rules.flush_due([], any_task_in_progress=False, now=NOW, day_end_at=DAY_END) is False
