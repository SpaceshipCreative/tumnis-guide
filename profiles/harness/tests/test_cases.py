"""Skill case files (P1-05): the 3-of-3 rule cannot be lowered by a case."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness.tests._cases import write_case

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.req("Quality: Hermes skills")
@pytest.mark.wp("P1-05")
def test_case_cannot_lower_runs(tmp_path: Path) -> None:
    """T-P1-05-09
    harness.toml fixes three runs per case; a case file with `runs: 1` is rejected when
    it is loaded, while the same case without `runs` loads.
    """
    from harness.cases import CaseError, load_case

    ok = load_case(write_case(tmp_path, "ok.yaml"))
    assert ok.id == "enrich-unit-human"
    assert ok.allow == ()

    for runs in (1, 3, 5):
        with pytest.raises(CaseError, match="runs"):
            load_case(write_case(tmp_path, f"runs_{runs}.yaml", runs=runs))
