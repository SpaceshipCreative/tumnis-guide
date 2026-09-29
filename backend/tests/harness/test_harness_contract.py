"""Harness self test, contract layer, so the layer never collects zero tests (P0-02)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

pytestmark = pytest.mark.contract

Recordings = Callable[[str], list[tuple[dict[str, Any], list[Any]]]]


@pytest.mark.req("Quality rule 5")
@pytest.mark.wp("P0-02")
def test_recordings_fixture_reads_folder_convention(recordings: Recordings) -> None:
    """T-P0-02-14
    recordings("demo") loads a demo folder into (raw, expected) pairs.
    """
    pairs = recordings("demo")
    assert len(pairs) == 2
    for raw, expected in pairs:
        assert isinstance(raw, dict)
        assert isinstance(expected, list)
        assert expected, "every recording names at least one expected record"
    raws = [raw for raw, _ in pairs]
    assert [raw["id"] for raw in raws] == ["evt-001", "evt-002"]  # sorted by file name
    assert pairs[0][1] == [{"type": "event", "external_id": "evt-001", "title": "Standup"}]

    with pytest.raises(FileNotFoundError, match="no-such-provider"):
        recordings("no-such-provider")
