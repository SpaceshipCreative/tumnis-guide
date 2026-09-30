"""No memory-system client in Tumnis (P2-03, FR-13.5): Tumnis feeds agent memory through
the digests and never talks to Hindsight itself. The lockfile has no Hindsight package,
and an import-linter forbidden contract refuses any import of one.
"""

from __future__ import annotations

import configparser
import re
import tomllib
from pathlib import Path
from typing import Final

import pytest

from tests.meta._lint_tree import LINT_PACKAGE, make_lint_tree, run_lint_imports

BACKEND = Path(__file__).resolve().parents[2]
LOCKFILE = BACKEND / "uv.lock"
IMPORTLINTER = BACKEND / ".importlinter"
CONTRACT = "importlinter:contract:no-memory-client"
MEMORY_PACKAGES: Final = ("hindsight", "hindsight_client", "vectorize")
HINDSIGHT = re.compile(r"hindsight", re.IGNORECASE)


@pytest.mark.req("FR-13.5")
@pytest.mark.wp("P2-03")
def test_no_hindsight_dependency_or_import(tmp_path: Path) -> None:
    """T-P2-03-10
    `uv.lock` has no package whose name matches `hindsight*`; `.importlinter` has the
    forbidden contract `no-memory-client` (source `tumnis`, forbidding `hindsight`,
    `hindsight_client` and `vectorize`); and a module that imports a Hindsight client
    breaks it.
    """
    lock = tomllib.loads(LOCKFILE.read_text())
    names = [package["name"] for package in lock.get("package", [])]
    assert names, "uv.lock lists no packages"
    assert [name for name in names if HINDSIGHT.search(name)] == []

    config = configparser.ConfigParser()
    config.read(IMPORTLINTER)
    assert config.has_section(CONTRACT)
    contract = config[CONTRACT]
    assert contract["type"] == "forbidden"
    assert contract["source_modules"].split() == ["tumnis"]
    assert sorted(contract["forbidden_modules"].split()) == sorted(MEMORY_PACKAGES)
    assert config["importlinter"].getboolean("include_external_packages")

    bad = f"{LINT_PACKAGE}/modules/agents/memory.py"
    lint = make_lint_tree(tmp_path, {bad: "import hindsight_client\n"})
    result = run_lint_imports(tmp_path, lint)
    assert result.returncode != 0, result.stdout + result.stderr
    assert f"{LINT_PACKAGE}.modules.agents.memory -> hindsight_client" in result.stdout
