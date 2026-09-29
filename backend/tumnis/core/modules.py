"""Module registry: the one place a module is registered (P0-08 adds flags here).

`scripts/new_module.py` appends new names to `MODULES`.
"""

from typing import Final

MODULES: Final[tuple[str, ...]] = (
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
SUBSCRIBE_ONLY: Final[frozenset[str]] = frozenset({"search", "usage"})
MODULE_FILES: Final[tuple[str, ...]] = (
    "api.py",
    "router.py",
    "mcp.py",
    "models.py",
    "rules.py",
    "workflows.py",
    "events.py",
)
MODULE_DIRS: Final[tuple[str, ...]] = ("adapters", "migrations", "tests")
