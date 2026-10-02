"""Install and operations docs stay runnable (P4-06, REL-4): the README's Install section and
OPERATIONS.md's upgrade and rollback commands are tagged blocks of the README harness
(scripts/readme_test.py), and the README job runs them (.github/workflows/readme.yml)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
HARNESS = REPO / "scripts" / "readme_test.py"
README = REPO / "README.md"
OPERATIONS = REPO / "docs" / "OPERATIONS.md"
README_YML = REPO / ".github" / "workflows" / "readme.yml"


def _harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location("readme_test", HARNESS)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["readme_test"] = module
    spec.loader.exec_module(module)
    return module


def _workflow() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(README_YML.read_text())
    if True in data:  # YAML 1.1 reads the bare key `on` as true
        data["on"] = data.pop(True)
    return data


def _runs(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in job.get("steps", []))


@pytest.mark.req("REL-4")
@pytest.mark.wp("P4-06")
def test_operations_doc_commands_are_tagged() -> None:
    """T-P4-06-08
    OPERATIONS.md's Upgrade and Rollback sections have no untagged shell block and at
    least one `readme:upgrade` block each; readme.yml runs on a schedule and its upgrade
    job runs the harness's `upgrade` suite.
    """
    readme = _harness()
    text = OPERATIONS.read_text()
    blocks = readme.extract(text)
    for section in ("Upgrade", "Rollback"):
        assert readme.check_section_coverage(text, section=section) == [], section
        lines = readme.section_lines(text, section)
        assert any(b.suite == "upgrade" and b.line in lines for b in blocks), section

    workflow = _workflow()
    assert workflow["on"]["schedule"]
    assert "--suite upgrade" in _runs(workflow["jobs"]["readme-upgrade"])


@pytest.mark.req("A4.4")
@pytest.mark.wp("P4-06")
def test_readme_install_is_tagged_and_run_on_a_clean_vm() -> None:
    """T-P4-06-05
    The README's Install section has no untagged shell block and its install suite is not
    empty; readme.yml's `readme-install` job runs on a fresh GitHub-hosted ubuntu-24.04 VM,
    for pull requests that touch the README, deploy/ or the harness, nightly and on release
    tags, and runs the install and first-run suites with the health check.
    """
    readme = _harness()
    text = README.read_text()
    assert readme.check_section_coverage(text) == []
    assert readme.select(readme.extract(text), "install")

    workflow = _workflow()
    paths = workflow["on"]["pull_request"]["paths"]
    for path in ("README.md", "deploy/**", "scripts/readme_test.py"):
        assert path in paths
    assert workflow["on"]["schedule"]
    assert workflow["on"]["push"]["tags"] == ["v*.*.*"]
    job = workflow["jobs"]["readme-install"]
    assert job["runs-on"] == "ubuntu-24.04"
    runs = _runs(job)
    for part in ("scripts/readme_test.py", "--suite install", "--suite first-run", "--health-url"):
        assert part in runs, part
