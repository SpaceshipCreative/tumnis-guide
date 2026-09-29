"""Module boundaries: import-linter contracts, Ruff banned-api, rules.py allow-list (P0-01)."""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from tests.meta._lint_tree import make_lint_tree, run_lint_imports

BACKEND = Path(__file__).resolve().parents[2]
MODULES_DIR = BACKEND / "tumnis" / "modules"
MODULE_COUNT = 15

RULES_ALLOWED = {
    "__future__",
    "dataclasses",
    "datetime",
    "zoneinfo",
    "enum",
    "typing",
    "typing_extensions",
    "collections",
    "collections.abc",
    "itertools",
    "functools",
    "math",
    "decimal",
    "fractions",
    "re",
    "uuid",
    "bisect",
    "heapq",
    "pydantic",
    "tumnis.core.types",
}


@pytest.mark.req("ADR-0001")
@pytest.mark.wp("P0-01")
def test_import_linter_rejects_cross_module_internal_import(tmp_path: Path) -> None:
    """T-P0-01-04
    The real .importlinter fails on a module importing another module's models.
    """
    config = make_lint_tree(
        tmp_path,
        {"lintpkg/modules/tasks/router.py": "from lintpkg.modules.projects import models\n"},
    )
    result = run_lint_imports(tmp_path, config)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "lintpkg.modules.tasks.router -> lintpkg.modules.projects.models" in result.stdout


@pytest.mark.req("ADR-0001")
@pytest.mark.wp("P0-01")
def test_import_linter_accepts_api_import(tmp_path: Path) -> None:
    """T-P0-01-05
    The same fixture importing only the other module's api passes.
    """
    config = make_lint_tree(
        tmp_path,
        {"lintpkg/modules/tasks/router.py": "from lintpkg.modules.projects import api\n"},
    )
    result = run_lint_imports(tmp_path, config)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.req("ADR-0001")
@pytest.mark.wp("P0-01")
def test_import_linter_rejects_io_in_rules(tmp_path: Path) -> None:
    """T-P0-01-06
    A rules.py importing sqlalchemy breaks the rules-are-pure contract.
    """
    config = make_lint_tree(
        tmp_path,
        {
            "lintpkg/modules/tasks/rules.py": "import sqlalchemy\n",
            "sqlalchemy/__init__.py": "",
        },
    )
    result = run_lint_imports(tmp_path, config)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "rules.py imports nothing that does I/O BROKEN" in result.stdout
    assert "lintpkg.modules.tasks.rules -> sqlalchemy" in result.stdout


@pytest.mark.req("ADR-0001")
@pytest.mark.wp("P0-01")
def test_import_linter_rejects_module_cycle(tmp_path: Path) -> None:
    """T-P0-01-07
    a.api -> b.api -> a.api breaks the module-graph-acyclic contract.
    """
    config = make_lint_tree(
        tmp_path,
        {
            "lintpkg/modules/tasks/api.py": "from lintpkg.modules.projects import api\n",
            "lintpkg/modules/projects/api.py": "from lintpkg.modules.tasks import api\n",
        },
    )
    result = run_lint_imports(tmp_path, config)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "No cycles between modules BROKEN" in result.stdout


def _ruff_on_stdin(filename: str, source: str) -> str:
    result = subprocess.run(  # noqa: S603 (fixed argv, no shell)
        [sys.executable, "-m", "ruff", "check", "--stdin-filename", filename, "-"],
        cwd=BACKEND,
        input=source,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout + result.stderr


@pytest.mark.req("ADR-0001")
@pytest.mark.wp("P0-01")
def test_ruff_bans_clock_calls_in_rules_only() -> None:
    """T-P0-01-08
    datetime.now() is TID251 in rules.py and allowed in api.py.
    """
    source = "from datetime import datetime\nx = datetime.now()\n"
    assert "TID251" in _ruff_on_stdin("tumnis/modules/demo/rules.py", source)
    assert "TID251" not in _ruff_on_stdin("tumnis/modules/demo/api.py", source)


def _imported_names(tree: ast.Module) -> list[tuple[int, str]]:
    names: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            names.append((node.lineno, prefix + (node.module or "")))
    return names


def _rules_import_allowed(module: str, name: str) -> bool:
    if name in RULES_ALLOWED:
        return True
    own_prefix = f"tumnis.modules.{module}."
    return name.startswith(own_prefix) and name[len(own_prefix) :].startswith("rules")


@pytest.mark.req("ADR-0001")
@pytest.mark.wp("P0-01")
def test_rules_files_import_only_allowlisted_modules() -> None:
    """T-P0-01-09
    Every tumnis/modules/*/rules*.py imports only RULES_ALLOWED or its own rules*;
    the shared pure types module exists and obeys the same allow-list.
    """
    rules_files = sorted(MODULES_DIR.glob("*/rules*.py"))
    assert len({path.parent.name for path in rules_files}) >= MODULE_COUNT
    violations = [
        f"{path.relative_to(BACKEND)}:{lineno} imports {name}"
        for path in rules_files
        for lineno, name in _imported_names(ast.parse(path.read_text()))
        if not _rules_import_allowed(path.parent.name, name)
    ]

    assert importlib.util.find_spec("tumnis.core.types") is not None
    types_path = BACKEND / "tumnis" / "core" / "types.py"
    violations += [
        f"{types_path.relative_to(BACKEND)}:{lineno} imports {name}"
        for lineno, name in _imported_names(ast.parse(types_path.read_text()))
        if name not in RULES_ALLOWED - {"tumnis.core.types"}
    ]
    assert not violations, "\n".join(violations)
