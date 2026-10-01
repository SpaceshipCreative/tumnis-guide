"""CI configuration: every job required and budgeted; coverage gates (P0-03)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.ci._scripts import REPO, load

CI_YML = REPO / ".github" / "workflows" / "ci.yml"
REQUIRED_CHECKS = REPO / ".github" / "required-checks.txt"

# Minutes per job: the A4 layer budgets (ARCHITECTURE.md CI table) and the guards' budgets
# from the P0-03 ci.yml skeleton.
BUDGETS = {
    "lint": 2,
    "unit": 4,
    "contract": 3,
    "integration": 15,
    "daemon": 3,  # runner daemon lint, types and tests (P1-04)
    "e2e": 10,
    "skills": 5,
    "security": 5,
    "performance": 5,
    "spec-guard": 2,
    "red-proof": 5,
    "traceability": 3,
}


@pytest.mark.req("Quality rule 1", "Quality rule 2", "Quality rule 4")
@pytest.mark.wp("P0-03")
def test_every_ci_job_is_required_and_budgeted() -> None:
    """T-P0-03-16
    Job names in ci.yml equal required-checks.txt; each job has timeout-minutes at its
    budget.
    """
    jobs: dict[str, dict[str, Any]] = yaml.safe_load(CI_YML.read_text())["jobs"]
    required = [
        line.strip()
        for line in REQUIRED_CHECKS.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]

    assert len(required) == len(set(required)), "duplicate names in required-checks.txt"
    assert set(jobs) == set(required) == set(BUDGETS)
    for name, job in jobs.items():
        assert "name" not in job, f"{name}: a display name would change the check name"
        assert job.get("timeout-minutes") == BUDGETS[name], name


@pytest.fixture(autouse=True)
def _no_ci_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


def _gate(tmp_path: Path, files: dict[str, tuple[int, int]]) -> int:
    """Run coverage_gates on a coverage.json with {path: (covered, statements)}."""
    coverage_gates = load("coverage_gates")
    report = {
        "files": {
            path: {
                "summary": {
                    "covered_lines": covered,
                    "num_statements": statements,
                    "missing_lines": statements - covered,
                    "percent_covered": 100.0 * covered / statements if statements else 100.0,
                }
            }
            for path, (covered, statements) in files.items()
        }
    }
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(report))
    code: int = coverage_gates.main([str(path)])
    return code


@pytest.mark.req("Quality rule 4")
@pytest.mark.wp("P0-03")
def test_coverage_gates_groups(tmp_path: Path) -> None:
    """T-P0-03-17
    coverage_gates fails at 79% on the rules group and at 99% on tasks/rules.py; empty
    groups pass.
    """
    rules, mcp = "tumnis/modules/projects/rules.py", "tumnis/modules/projects/mcp.py"
    tasks = "tumnis/modules/tasks/rules.py"
    other = "tumnis/core/db.py"

    assert _gate(tmp_path, {rules: (79, 100), other: (0, 50)}) == 1
    assert _gate(tmp_path, {rules: (60, 80), mcp: (19, 20)}) == 1  # 79 of 100 is 79%
    assert _gate(tmp_path, {rules: (60, 80), mcp: (20, 20)}) == 0  # 80 of 100 passes
    assert _gate(tmp_path, {rules: (80, 100), tasks: (99, 100)}) == 1
    assert _gate(tmp_path, {rules: (80, 100), tasks: (100, 100)}) == 0

    assert _gate(tmp_path, {}) == 0
    assert _gate(tmp_path, {rules: (0, 0), tasks: (0, 0), other: (0, 50)}) == 0


@pytest.mark.req("Quality rule 1")
@pytest.mark.wp("P0-03")
def test_spec_guard_job_compares_with_current_base_branch() -> None:
    """T-P0-03-22
    Red-proof for #79 and #81: the spec-guard job never uses the event's
    `pull_request.base.sha` (it can predate a main the PR has since merged). With full
    history, it fetches the base branch and passes it as `origin/<base_ref>`, and the
    script compares against the merge-base with it.
    """
    job: dict[str, Any] = yaml.safe_load(CI_YML.read_text())["jobs"]["spec-guard"]
    steps: list[dict[str, Any]] = job["steps"]
    names = [step.get("name") for step in steps]
    guard = steps[names.index("spec-guard")]
    before = steps[: names.index("spec-guard")]

    assert "pull_request.base.sha" not in yaml.safe_dump(job)
    checkout = next(s for s in before if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 0
    base_vars = [k for k, v in guard["env"].items() if v == "${{ github.base_ref }}"]
    assert len(base_vars) == 1, "spec-guard needs the base branch name from github.base_ref"
    assert f'--base "origin/${base_vars[0]}"' in guard["run"]
    fetches = [
        s
        for s in before
        if "git fetch" in s.get("run", "")
        and "${{ github.base_ref }}" in (s.get("env") or {}).values()
        and "refs/remotes/origin/" in s["run"]
    ]
    assert fetches, "a step before spec-guard fetches the current base branch"
