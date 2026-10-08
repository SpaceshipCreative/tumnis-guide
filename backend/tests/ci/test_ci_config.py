"""CI configuration: every job required and budgeted; coverage gates (P0-03)."""

from __future__ import annotations

import json
import shlex
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.ci._scripts import REPO, load

CI_YML = REPO / ".github" / "workflows" / "ci.yml"
DOCLING_YML = REPO / ".github" / "workflows" / "docling.yml"
REQUIRED_CHECKS = REPO / ".github" / "required-checks.txt"

# Minutes per job: the A4 layer budgets (ARCHITECTURE.md CI table) and the guards' budgets
# from the P0-03 ci.yml skeleton.
BUDGETS = {
    "lint": 2,
    "unit": 4,
    "unit-frontend": 4,  # frontend Vitest, split out of unit (Scott decision 96)
    "contract": 3,
    "integration-a": 15,  # two parallel jobs split by path (Scott decision 65)
    "integration-b": 15,
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
    Job names in ci.yml plus docling.yml's `docling` job equal required-checks.txt; each
    ci.yml job has timeout-minutes at its budget, and `docling` has a timeout-minutes.

    Scott decision 101 widened only the list check, to admit `docling`; the rest is as
    before.
    """
    jobs: dict[str, dict[str, Any]] = yaml.safe_load(CI_YML.read_text())["jobs"]
    docling: dict[str, Any] = yaml.safe_load(DOCLING_YML.read_text())["jobs"]["docling"]
    required = [
        line.strip()
        for line in REQUIRED_CHECKS.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]

    assert len(required) == len(set(required)), "duplicate names in required-checks.txt"
    assert set(jobs) == set(BUDGETS)
    assert set(required) == set(jobs) | {"docling"}
    for name, job in jobs.items():
        assert "name" not in job, f"{name}: a display name would change the check name"
        assert job.get("timeout-minutes") == BUDGETS[name], name
    assert "timeout-minutes" in docling, "docling: no timeout-minutes"


PYPROJECT = REPO / "backend" / "pyproject.toml"
INTEGRATION = "integration and not contract"
# pytest options that take their value as the next word.
_VALUE_OPTIONS = {"-m", "-n", "-k", "-p", "--dist", "--ignore", "--durations"}


def _pytest_steps() -> list[tuple[str, str, set[Path], set[Path]]]:
    """(job, -m expression, selected roots, ignored roots) for every `pytest` line in ci.yml.

    A command without paths selects pyproject's testpaths, as pytest does when it runs
    from the rootdir (every pytest step runs in backend/).
    """
    backend = REPO / "backend"
    pytest_ini = tomllib.loads(PYPROJECT.read_text())["tool"]["pytest"]["ini_options"]
    jobs: dict[str, dict[str, Any]] = yaml.safe_load(CI_YML.read_text())["jobs"]
    found = []
    for job_name, job in jobs.items():
        for step in job.get("steps", []):
            for line in str(step.get("run", "")).splitlines():
                if not line.strip().startswith("uv run pytest "):
                    continue
                words = shlex.split(line, comments=True)
                marker, paths, ignored = "", set(), set()
                rest = iter(words[3:])
                for word in rest:
                    option, _, value = word.partition("=")
                    if option in _VALUE_OPTIONS and not value:
                        value = next(rest)
                    if option == "-m":
                        marker = value
                    elif option == "--ignore":
                        ignored.add((backend / value).resolve())
                    elif not word.startswith("-"):
                        paths.add((backend / word).resolve())
                roots = paths or {(backend / p).resolve() for p in pytest_ini["testpaths"]}
                found.append((job_name, marker, roots, ignored))
    return found


def _test_files() -> set[Path]:
    pytest_ini = tomllib.loads(PYPROJECT.read_text())["tool"]["pytest"]["ini_options"]
    files: set[Path] = set()
    for testpath in pytest_ini["testpaths"]:
        root = (REPO / "backend" / testpath).resolve()
        for pattern in ("test_*.py", "*_test.py"):
            files.update(
                f
                for f in root.rglob(pattern)
                if not {".venv", "node_modules", "__pycache__"} & set(f.parts)
            )
    return files


def _selects(roots: set[Path], ignored: set[Path], file: Path) -> bool:
    def under(parents: set[Path]) -> bool:
        return any(file == p or p in file.parents for p in parents)

    return under(roots) and not under(ignored)


@pytest.mark.req("Quality rule 1", "Quality rule 2")
@pytest.mark.wp("P0-03")
def test_integration_jobs_run_every_test_file_once() -> None:
    """T-P0-03-23
    Scott decision 65: the integration layer runs as two parallel jobs split by path, and
    the serial (timing budget) step runs in one of them. Every test file under testpaths
    is selected by exactly one parallel step and exactly one serial step, so a new module
    cannot fall out of both jobs or run twice.
    """
    steps = [s for s in _pytest_steps() if s[1].startswith(INTEGRATION)]
    parallel = [s for s in steps if s[1] == f"{INTEGRATION} and not serial"]
    serial = [s for s in steps if s[1] == f"{INTEGRATION} and serial"]

    assert len(steps) == len(parallel) + len(serial), [s[:2] for s in steps]
    assert sorted(job for job, *_ in parallel) == ["integration-a", "integration-b"]
    assert len(serial) == 1, "the serial step runs in exactly one integration job"
    assert serial[0][0] in {"integration-a", "integration-b"}
    for job, _, roots, ignored in steps:
        for path in roots | ignored:
            assert path.exists(), f"{job}: {path} does not exist"

    files = _test_files()
    assert files
    for stage in (parallel, serial):
        for file in sorted(files):
            jobs = [job for job, _, roots, ignored in stage if _selects(roots, ignored, file)]
            assert len(jobs) == 1, f"{file.relative_to(REPO)} runs in {jobs or 'no job'}"


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
