"""Import the CI scripts in scripts/ci (outside the backend package) by module name.

Tests import inside their bodies, so a missing script fails the test, not collection.
"""

from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[3]
SCRIPTS_CI = REPO / "scripts" / "ci"
TYPESCRIPT = REPO / "frontend" / "node_modules" / "typescript"


def load(name: str) -> ModuleType:
    if str(SCRIPTS_CI) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_CI))
    return importlib.import_module(name)


def node_with_typescript() -> bool:
    """Node on PATH and the frontend's TypeScript installed (`npm ci` in frontend/)."""
    return shutil.which("node") is not None and TYPESCRIPT.is_dir()
