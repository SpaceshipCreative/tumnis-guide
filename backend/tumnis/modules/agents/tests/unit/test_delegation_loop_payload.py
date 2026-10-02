"""The `delegation_loop` review item's payload holds any number of stopped runs (P2-06,
SAF-5): a bounded sample of their ids plus the full count, so a caller with many active
runs still gets its review item."""

from __future__ import annotations

import uuid

import pytest


@pytest.mark.req("SAF-5")
@pytest.mark.wp("P2-06")
@pytest.mark.parametrize("stopped", [0, 3, 100, 101, 250])
def test_payload_keeps_a_sample_and_the_full_count(stopped: int) -> None:
    from tumnis.modules.agents.review_kinds import (  # noqa: PLC0415
        STOPPED_RUNS_SAMPLE,
        DelegationLoopPayload,
    )

    runs = [uuid.uuid4() for _ in range(stopped)]
    payload = DelegationLoopPayload.of(uuid.uuid4(), delegations=2, cycle=False, stopped=runs)
    dumped = DelegationLoopPayload.model_validate(payload.model_dump(mode="json"))
    assert dumped.stopped_count == stopped
    assert dumped.stopped_runs == runs[:STOPPED_RUNS_SAMPLE]
    assert STOPPED_RUNS_SAMPLE == 100
