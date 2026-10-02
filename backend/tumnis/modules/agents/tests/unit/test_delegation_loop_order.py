"""The loop count resets when a result is accepted, at the time it is accepted (P2-06,
SAF-5): an older delegation's result accepted after a newer delegation still counts as
"an accepted result in between" for the delegations that follow it."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

NOW = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


def _ago(minutes: float) -> datetime:
    return NOW - timedelta(minutes=minutes)


@pytest.mark.req("SAF-5")
@pytest.mark.wp("P2-06")
def test_a_late_acceptance_resets_the_count_at_its_own_time() -> None:
    """D1 (50 min ago), D2 (40), D1's result accepted (35), D3 (20): a fourth delegation
    now follows only D3 since the acceptance, so it is no loop. With D1's result accepted
    after D3 instead (10), the acceptance still comes last: no loop either."""
    from tumnis.modules.agents.rules import DelegationRecord, is_delegation_loop  # noqa: PLC0415

    task = uuid.uuid4()

    def history(accepted_minutes_ago: float) -> list[DelegationRecord]:
        return [
            DelegationRecord(
                delegation_id=uuid.uuid4(),
                task_id=task,
                delegated_at=_ago(50),
                accepted=True,
                accepted_at=_ago(accepted_minutes_ago),
            ),
            DelegationRecord(delegation_id=uuid.uuid4(), task_id=task, delegated_at=_ago(40)),
            DelegationRecord(delegation_id=uuid.uuid4(), task_id=task, delegated_at=_ago(20)),
        ]

    assert not is_delegation_loop(history(35), task, NOW)
    assert not is_delegation_loop(history(10), task, NOW)


@pytest.mark.req("SAF-5")
@pytest.mark.wp("P2-06")
def test_an_acceptance_before_two_delegations_still_loops() -> None:
    """D1 accepted (45 min ago), then D2 (30) and D3 (10) with nothing accepted: the next
    delegation is the third since the acceptance, a loop."""
    from tumnis.modules.agents.rules import DelegationRecord, is_delegation_loop  # noqa: PLC0415

    task = uuid.uuid4()
    records = [
        DelegationRecord(
            delegation_id=uuid.uuid4(),
            task_id=task,
            delegated_at=_ago(50),
            accepted=True,
            accepted_at=_ago(45),
        ),
        DelegationRecord(delegation_id=uuid.uuid4(), task_id=task, delegated_at=_ago(30)),
        DelegationRecord(delegation_id=uuid.uuid4(), task_id=task, delegated_at=_ago(10)),
    ]
    assert is_delegation_loop(records, task, NOW)
