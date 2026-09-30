"""The acceptance suites' own fixtures: no test leaves decision fakes behind for the next
(`_phase1.script_label` and `fail_decision_providers` set them process-wide, P1-07)."""

from collections.abc import Iterator

import pytest

from tests._labels import reset_label_fakes


@pytest.fixture(autouse=True)
def _reset_label_fakes() -> Iterator[None]:
    yield
    reset_label_fakes()
