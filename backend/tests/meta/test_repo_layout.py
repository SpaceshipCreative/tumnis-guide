"""Repo layout, module shape and module registry (P0-01)."""

from __future__ import annotations

import ast
import configparser
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
MODULES_DIR = BACKEND / "tumnis" / "modules"

# A1: the 15 modules the architecture names.
A1_MODULES = (
    "auth",
    "projects",
    "tasks",
    "planning",
    "agents",
    "focus",
    "decisions",
    "search",
    "knowledge",
    "integrations",
    "calendar",
    "github",
    "coolify",
    "notifications",
    "usage",
)
# A2: every module has these files and folders.
A2_FILES = ("api.py", "router.py", "mcp.py", "models.py", "rules.py", "workflows.py", "events.py")
A2_DIRS = ("adapters", "migrations", "tests")
A2_TEST_DIRS = ("unit", "integration", "contract", "recordings")
TOP_LEVEL_FOLDERS = (
    "backend",
    "frontend",
    "daemon",
    "profiles",
    "schemas",
    "deploy",
    "scripts",
    "docs",
)
ADR_HEADINGS = ("## Context", "## Options considered", "## Decision", "## Consequences", "## Sources")
AGENTS_HEADINGS = (
    "## TDD rules",
    "## Module boundaries",
    "## Where things go",
    "## Tests and markers",
    "## Adapters and fakes",
    "## Time",
    "## Commands",
    "## Never",
)
ADR_COUNT = 11


def _module_folders() -> set[str]:
    if not MODULES_DIR.is_dir():
        return set()
    return {
        path.name
        for path in MODULES_DIR.iterdir()
        if path.is_dir() and not path.name.startswith(("_", "."))
    }


def _is_package_or_kept(directory: Path) -> bool:
    return (directory / "__init__.py").is_file() or (directory / ".gitkeep").is_file()


@pytest.mark.req("ADR-0001")
@pytest.mark.wp("P0-01")
@pytest.mark.xfail(strict=True, reason="spec:P0-01")
def test_top_level_folders_exist() -> None:
    """T-P0-01-01
    The A1 top-level folders exist at the repo root.
    """
    missing = [name for name in TOP_LEVEL_FOLDERS if not (REPO / name).is_dir()]
    assert not missing, f"missing top-level folders: {missing}"


@pytest.mark.req("ADR-0001")
@pytest.mark.wp("P0-01")
@pytest.mark.parametrize("module", sorted(set(A1_MODULES) | _module_folders()))
def test_every_module_has_standard_shape(module: str) -> None:
    """T-P0-01-02
    Each module has the 7 A2 files and 3 folders; tests/ has unit, integration,
    contract and recordings. Folders are packages or carry a .gitkeep.
    """
    root = MODULES_DIR / module
    init = root / "__init__.py"
    assert init.is_file(), f"{module}: missing __init__.py"
    for filename in A2_FILES:
        assert (root / filename).is_file(), f"{module}: missing {filename}"
    for dirname in A2_DIRS:
        assert (root / dirname).is_dir(), f"{module}: missing {dirname}/"
        assert _is_package_or_kept(root / dirname), f"{module}/{dirname}: no __init__ or .gitkeep"
    for dirname in A2_TEST_DIRS:
        test_dir = root / "tests" / dirname
        assert test_dir.is_dir(), f"{module}: missing tests/{dirname}/"
        assert _is_package_or_kept(test_dir), f"{module}/tests/{dirname}: no __init__ or .gitkeep"
    # A re-export in __init__ would let callers bypass the api-only rule.
    tree = ast.parse(init.read_text())
    imports = [node for node in tree.body if isinstance(node, ast.Import | ast.ImportFrom)]
    assert not imports, f"{module}/__init__.py must not import anything"


@pytest.mark.req("ADR-0001")
@pytest.mark.wp("P0-01")
@pytest.mark.xfail(strict=True, reason="spec:P0-01")
def test_module_registry_matches_folders_and_contract() -> None:
    """T-P0-01-03
    MODULES equals the module folder set and the independence contract's module list.
    """
    from tumnis.core.modules import MODULE_DIRS, MODULE_FILES, MODULES  # noqa: PLC0415

    assert len(MODULES) == len(set(MODULES)), "MODULES has duplicates"
    assert set(A1_MODULES) <= set(MODULES)
    assert set(MODULES) == _module_folders()
    assert MODULE_FILES == A2_FILES
    assert MODULE_DIRS == A2_DIRS

    parser = configparser.ConfigParser()
    parser.read(BACKEND / ".importlinter")
    section = parser["importlinter:contract:modules-api-only"]
    assert section["type"] == "independence"
    listed = [line.strip() for line in section["modules"].splitlines() if line.strip()]
    assert listed == [f"tumnis.modules.{name}" for name in MODULES]


@pytest.mark.req("ADR-0001")
@pytest.mark.wp("P0-01")
@pytest.mark.xfail(strict=True, reason="spec:P0-01")
def test_adr_files_and_agent_rules_present() -> None:
    """T-P0-01-11
    11 ADR files with the template headings; AGENTS.md has the required sections;
    CLAUDE.md points to AGENTS.md.
    """
    adr_dir = REPO / "docs" / "adr"
    assert (adr_dir / "0001-modular-monolith.md").is_file()
    assert (adr_dir / "0011-outbox-dbos-dedup.md").is_file()
    for number in range(1, ADR_COUNT + 1):
        matches = sorted(adr_dir.glob(f"{number:04d}-*.md"))
        assert len(matches) == 1, f"expected one ADR {number:04d}, found {matches}"
        text = matches[0].read_text()
        lines = text.splitlines()
        assert lines[0].startswith(f"# ADR-{number:04d}: "), matches[0].name
        assert re.search(r"^Status: ", text, re.MULTILINE), f"{matches[0].name}: no Status line"
        if number in {10, 11}:
            assert re.search(r"^Status: Proposed", text, re.MULTILINE), matches[0].name
        for heading in ADR_HEADINGS:
            assert heading in lines, f"{matches[0].name}: missing {heading!r}"

    agents_lines = (REPO / "AGENTS.md").read_text().splitlines()
    for heading in AGENTS_HEADINGS:
        assert heading in agents_lines, f"AGENTS.md: missing {heading!r}"
    assert "AGENTS.md" in (REPO / "CLAUDE.md").read_text()
