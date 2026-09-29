"""traceability: a finished phase's requirements each have a passing test (P0-03, rule 4)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from tests.ci._scripts import load

DATA = Path(__file__).parent / "data"


def _ref(nodeid: str, reqs: list[str], *, expected_failure: bool) -> dict[str, Any]:
    return {
        "id": nodeid,
        "reqs": reqs,
        "wp": "P0-14",
        "tid": "T-P0-14-01",
        "expected_failure": expected_failure,
    }


def _trace(tmp_path: Path, refs: list[dict[str, Any]], plan: str) -> tuple[int, str]:
    """Run traceability on a plan fixture and a fake trace.json; returns (exit, markdown)."""
    traceability = load("traceability")
    trace, out = tmp_path / "trace.json", tmp_path / "trace.md"
    trace.write_text(json.dumps(refs))
    code: int = traceability.main(
        [
            "--repo",
            str(tmp_path),  # no frontend/ here, so no Vitest or Playwright refs
            "--plan",
            str(DATA / plan),
            "--prd",
            str(DATA / "prd.md"),
            "--trace-json",
            str(trace),
            "--out",
            str(out),
        ]
    )
    return code, out.read_text()


def _section(markdown: str, title: str) -> str:
    """The body of the `## <title>...` section, up to the next `## ` heading."""
    match = re.search(rf"^## {re.escape(title)}.*?$(.*?)(?=^## |\Z)", markdown, re.M | re.S)
    assert match, f"no '## {title}' section in:\n{markdown}"
    return match.group(1)


PASSING = [
    _ref("tests/test_a.py::test_projects", ["FR-1.1", "J2"], expected_failure=False),
    _ref("tests/test_a.py::test_jev_rate", ["FR-11.9"], expected_failure=False),
]


@pytest.mark.req("Quality rule 4")
@pytest.mark.wp("P0-03")
def test_fails_when_done_phase_requirement_has_no_passing_test(tmp_path: Path) -> None:
    """T-P0-03-13
    Phase 0 done and SEC-2 tagged only on a spec-xfail test: exit 1, SEC-2 under Missing.
    """
    refs = [*PASSING, _ref("tests/test_keys.py::test_keys", ["SEC-2"], expected_failure=True)]

    code, markdown = _trace(tmp_path, refs, "plan_done.yaml")

    assert code == 1
    missing = _section(markdown, "Missing")
    assert "SEC-2" in missing
    assert "FR-1.1" not in missing


@pytest.mark.req("Quality rule 4")
@pytest.mark.wp("P0-03")
def test_active_phase_gaps_are_reported_not_failed(tmp_path: Path) -> None:
    """T-P0-03-14
    A requirement with no passing test in an active phase is in the report; exit 0.
    """
    refs = [*PASSING, _ref("tests/test_keys.py::test_keys", ["SEC-2"], expected_failure=True)]

    code, markdown = _trace(tmp_path, refs, "plan_active.yaml")

    assert code == 0
    assert "SEC-2" in markdown
    assert "SEC-2" not in _section(markdown, "Missing")


@pytest.mark.req("Quality rule 4")
@pytest.mark.wp("P0-03")
def test_unknown_requirement_id_fails(tmp_path: Path) -> None:
    """T-P0-03-15
    A tag FR-3.33 (not in the PRD) fails as unknown, even with no phase done.
    """
    refs = [*PASSING, _ref("tests/test_tasks.py::test_typo", ["FR-3.33"], expected_failure=False)]

    code, markdown = _trace(tmp_path, refs, "plan_active.yaml")

    assert code == 1
    assert "FR-3.33" in _section(markdown, "Unknown")
