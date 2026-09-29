"""Runner presence from heartbeats (P1-04, FR-5.9): three missed 15-second beats."""

from datetime import UTC, datetime, timedelta

import pytest

from tumnis.modules.agents.rules import runner_status

LAST_BEAT = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


@pytest.mark.req("FR-5.9")
@pytest.mark.wp("P1-04")
@pytest.mark.xfail(strict=True, reason="spec:P1-04")
def test_three_missed_heartbeats_mean_offline() -> None:
    """T-P1-04-04
    45 s after the last beat the runner is online; 45 s + 1 µs after it, offline; a runner
    that never beat is never_seen.
    """
    assert runner_status(LAST_BEAT, LAST_BEAT) == "online"
    assert runner_status(LAST_BEAT, LAST_BEAT + timedelta(seconds=15)) == "online"
    assert runner_status(LAST_BEAT, LAST_BEAT + timedelta(seconds=45)) == "online"
    assert runner_status(LAST_BEAT, LAST_BEAT + timedelta(seconds=45, microseconds=1)) == (
        "offline"
    )
    assert runner_status(LAST_BEAT, LAST_BEAT + timedelta(hours=1)) == "offline"
    assert runner_status(None, LAST_BEAT) == "never_seen"
