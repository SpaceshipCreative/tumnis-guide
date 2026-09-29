"""Module registry: the one place a module is registered (P0-08 adds flags here).

`scripts/new_module.py` appends new names to `MODULES`.
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Final
from uuid import UUID

if TYPE_CHECKING:
    from tumnis.core.tenancy import WorkspaceContext
    from tumnis.settings import Settings

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

# --- Module flags (P0-08, Hosted readiness) ----------------------------------------------

REQUIRED_MODULES: Final = frozenset({"auth", "projects", "tasks"})  # plan default


class ModuleRequired(ValueError):  # noqa: N818  # the plan's name
    """A required module cannot be switched off."""


def deployment_disabled(settings: "Settings") -> frozenset[str]:
    raise NotImplementedError


def set_deployment_disabled(names: frozenset[str]) -> None:
    raise NotImplementedError


async def enabled(module: str, workspace_id: UUID) -> bool:
    raise NotImplementedError


async def set_module_enabled(ctx: "WorkspaceContext", module: str, on: bool) -> None:
    raise NotImplementedError


def require_module(module: str) -> Callable[..., Awaitable[None]]:
    raise NotImplementedError
