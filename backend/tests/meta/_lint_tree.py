"""Build a throwaway package tree that mirrors `tumnis` for import-linter fixture tests."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
REAL_CONFIG = BACKEND / ".importlinter"
LINT_PACKAGE = "lintpkg"
SKELETON_FILES = ("api.py", "models.py", "rules.py", "router.py")


def make_lint_tree(tmp_path: Path, extra: dict[str, str]) -> Path:
    """Create `lintpkg/` with core and every registered module, plus a rewritten `.importlinter`.

    `extra` maps paths relative to `tmp_path` (for example
    `lintpkg/modules/tasks/router.py`) to file contents; they overwrite the skeleton.
    Returns the path of the generated `.importlinter`.
    """
    from tumnis.core.modules import MODULES  # noqa: PLC0415 (spec tests must fail, not error)

    root = tmp_path / LINT_PACKAGE
    for package in (root, root / "core", root / "modules"):
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text("")
    for name in MODULES:
        module_dir = root / "modules" / name
        module_dir.mkdir(exist_ok=True)
        (module_dir / "__init__.py").write_text("")
        for filename in SKELETON_FILES:
            (module_dir / filename).write_text(f'"""{name}.{filename}"""\n')
    for dotted in named_modules(REAL_CONFIG.read_text()):
        _touch_module(tmp_path, dotted)
    for relative, content in extra.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    config = tmp_path / ".importlinter"
    config.write_text(REAL_CONFIG.read_text().replace("tumnis", LINT_PACKAGE))
    return config


def named_modules(config: str) -> list[str]:
    """Every module under `tumnis.modules` the config names without a wildcard, for
    example a protected contract's allowed importer (`tumnis.modules.agents.workflows`,
    P1-03): import-linter refuses a contract whose named modules are not in the graph."""
    names = re.findall(r"\btumnis(?:\.\w+)+(?![\w.*])", config)
    return sorted({name for name in names if name.startswith("tumnis.modules.")})


def _touch_module(tmp_path: Path, dotted: str) -> None:
    """An empty `lintpkg` module for `dotted` (a tumnis name), with package `__init__`s on
    the way; an existing module or package is left alone."""
    parts = [LINT_PACKAGE, *dotted.split(".")[1:]]
    package = tmp_path
    for part in parts[:-1]:
        package = package / part
        package.mkdir(exist_ok=True)
        init = package / "__init__.py"
        if not init.exists():
            init.write_text("")
    target = package / f"{parts[-1]}.py"
    if not target.exists() and not (package / parts[-1]).is_dir():
        target.write_text(f'"""{dotted}"""\n')


def run_lint_imports(tmp_path: Path, config: Path) -> subprocess.CompletedProcess[str]:
    """Run the installed `lint-imports` against the tree in `tmp_path`."""
    lint_imports = Path(sys.executable).parent / "lint-imports"
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    return subprocess.run(  # noqa: S603 (fixed argv, no shell)
        [str(lint_imports), "--config", str(config), "--no-cache"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
