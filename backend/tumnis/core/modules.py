"""Module registry: the one place a module is registered, and the module flags (P0-08).

`scripts/new_module.py` appends new names to `MODULES`.

A module can be switched off for the whole deployment (TUMNIS_DISABLED_MODULES) or for one
workspace (a `module_flags` row); the deployment flag wins, no row means on, and the
required modules cannot be switched off. A disabled module's routes answer 404
`not_found` (not a 403 that reveals the feature) and the relay skips its subscribers.
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Final
from uuid import UUID

from sqlalchemy import text

from tumnis.core import audit, tenancy
from tumnis.core.cache import CacheKey, CacheSpec, invalidate_on_commit, register_cache
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.errors import ProblemError
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.settings import SettingsError

if TYPE_CHECKING:
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
MODULE_FLAGS_CACHE = register_cache(
    CacheSpec("module_flags", scope="workspace", ttl_s=None, invalidated_by=("set_module_enabled",))
)
_ON, _OFF = b"1", b"0"


class ModuleRequired(SettingsError):  # noqa: N818  # the plan's name
    """A required module cannot be switched off (a SettingsError: the CLI exits 78 when the
    deployment kill list names one)."""

    def __init__(self, module: str) -> None:
        super().__init__("module_required", f"{module} is required and cannot be disabled")
        self.module = module


def deployment_disabled(settings: "Settings") -> frozenset[str]:
    """TUMNIS_DISABLED_MODULES="calendar,github" as a set. An unknown name is a SettingsError
    (a typo would otherwise leave the module on); a required one is ModuleRequired."""
    names = frozenset(
        name.strip() for name in settings.tumnis_disabled_modules.split(",") if name.strip()
    )
    unknown = sorted(names - set(MODULES))
    if unknown:
        raise SettingsError("unknown_module", f"TUMNIS_DISABLED_MODULES names {unknown}")
    for module in sorted(names & REQUIRED_MODULES):
        raise ModuleRequired(module)
    return names


_deployment_disabled: frozenset[str] = frozenset()


def set_deployment_disabled(names: frozenset[str]) -> None:
    global _deployment_disabled  # noqa: PLW0603  # one deployment per process
    _deployment_disabled = names


def configure(settings: "Settings") -> None:
    """The api and the worker read the deployment kill list at start."""
    set_deployment_disabled(deployment_disabled(settings))


def _flag_key(workspace_id: UUID, module: str) -> CacheKey:
    return CacheKey.for_workspace(workspace_id, "module_flags", module)


_READ_FLAG = text("SELECT enabled FROM module_flags WHERE module = :m AND deleted_at IS NULL")
_WRITE_FLAG = text(
    "INSERT INTO module_flags (module, enabled) VALUES (:m, :on) "
    "ON CONFLICT (workspace_id, module) DO UPDATE SET enabled = EXCLUDED.enabled"
)


async def enabled(module: str, workspace_id: UUID) -> bool:
    """The deployment flag wins; then the workspace's module_flags row (through the
    module_flags cache); no row means on. The relay asks this for every subscriber."""
    if module in _deployment_disabled:
        return False
    if module in REQUIRED_MODULES:
        return True
    key = _flag_key(workspace_id, module)
    cached = await MODULE_FLAGS_CACHE.get(key)
    if cached is None:
        token = MODULE_FLAGS_CACHE.token()
        async with tenant_session(WorkspaceContext(workspace_id, SYSTEM_ACTOR)) as session:
            flag = (await session.execute(_READ_FLAG, {"m": module})).scalar_one_or_none()
        cached = _OFF if flag is False else _ON
        await MODULE_FLAGS_CACHE.fill(key, cached, since=token)
    return cached == _ON


async def set_module_enabled(
    ctx: WorkspaceContext, module: str, on: bool, *, clock: Clock | None = None
) -> None:
    """Switch a module on or off for the workspace in `ctx` (ModuleRequired for a required
    module), audited as `module.toggled` (SEC-3); every process drops its cached flag on
    commit."""
    if module not in MODULES:
        raise ValueError(f"unknown module {module!r}")
    if not on and module in REQUIRED_MODULES:
        raise ModuleRequired(module)
    async with tenant_session(ctx) as session:
        await session.execute(_WRITE_FLAG, {"m": module, "on": on})
        await audit.record(
            session,
            "module.toggled",
            details={"module": module, "enabled": on},
            occurred_at=(clock or SystemClock()).now(),
        )
        await invalidate_on_commit(session, _flag_key(ctx.workspace_id, module))


def require_module(module: str) -> Callable[..., Awaitable[None]]:
    """FastAPI dependency: 404 problem `not_found` when the module is off for the deployment
    or for the caller's workspace."""

    async def module_enabled() -> None:
        ctx = tenancy.current()
        if module in _deployment_disabled or (
            ctx is not None and not await enabled(module, ctx.workspace_id)
        ):
            raise ProblemError(404, "not_found", "Not found")

    return module_enabled
