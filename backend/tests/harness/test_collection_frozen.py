"""What collection built is out of the garbage collector's reach (ci-health, T-P1-07-01).

Every run collects the whole suite, so a full collection walked about half a million
objects: a stop-the-world pause of about 330 ms on a CI runner, which landed inside the
label-latency test's measurement and made its p95 flaky. The harness freezes them once
collection finishes (`pytest_collection_finish` in tests/fixtures).
"""

from __future__ import annotations

import gc

import pytest

# Far fewer than the suite's collected objects (about 475,000 in CI), far more than a bare
# interpreter with pytest loaded holds.
AT_LEAST_FROZEN = 50_000


@pytest.mark.req("Quality rule 5")
@pytest.mark.wp("P0-02")
def test_collected_suite_is_frozen_out_of_gc() -> None:
    assert gc.get_freeze_count() >= AT_LEAST_FROZEN
