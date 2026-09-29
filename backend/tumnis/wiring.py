"""Composition root: imports every module's adapters, api, events and workflows so they
register (adapters, seed writers, event payload types, subscribers and DBOS workflows),
and registers each module's optional `api.health()` as a non-critical readiness check
(later: routers). Driven by the module registry, so a new module is wired automatically."""

import importlib
from typing import Protocol

import tumnis.core.settings_store  # noqa: F401  # registers the settings cache (P0-08)
from tumnis.core.adapters.registry import health_states, registered
from tumnis.core.health import HealthCheck, Status, register_health
from tumnis.core.metrics import register_scrape_source
from tumnis.core.modules import MODULES


class RegisterHealth(Protocol):
    """The shape of `tumnis.core.health.register_health` (P0-04)."""

    def __call__(self, name: str, check: HealthCheck, *, critical: bool) -> None: ...


def load_adapters() -> None:
    for module in MODULES:
        importlib.import_module(f"tumnis.modules.{module}.adapters")


def load_apis() -> None:
    """Import every module's api, so seed writers (and later subscribers) register."""
    for module in MODULES:
        importlib.import_module(f"tumnis.modules.{module}.api")


def load_events() -> None:
    """Import every module's events, so its payload types and subscribers register (the
    worker's relay enqueues a delivery per registered subscriber)."""
    for module in MODULES:
        importlib.import_module(f"tumnis.modules.{module}.events")


def load_workflows() -> None:
    """Import every module's workflows, so DBOS knows them before launch (a restarted
    worker recovers only registered workflows)."""
    for module in MODULES:
        importlib.import_module(f"tumnis.modules.{module}.workflows")


def register_module_health() -> None:
    """A module that defines `async def health() -> Status` in its api degrades readiness
    when it fails, never takes it down."""
    for module in MODULES:
        check = getattr(importlib.import_module(f"tumnis.modules.{module}.api"), "health", None)
        if check is not None:
            register_health(f"module:{module}", check, critical=False)


def register_module_metrics() -> None:
    """A module that defines `async def export_metrics(conn: AsyncConnection) -> None` in
    its api is called on every /metrics scrape with the app-role connection (P0-27)."""
    for module in MODULES:
        export = getattr(
            importlib.import_module(f"tumnis.modules.{module}.api"), "export_metrics", None
        )
        if export is not None:
            register_scrape_source(f"module:{module}", export)


def register_adapter_health(register: RegisterHealth) -> None:
    """Register `adapter:<name>` as a non-critical readiness check for every adapter.

    Called with `tumnis.core.health.register_health` once that exists (P0-04): an open
    breaker degrades readiness, it never takes the service down.
    """
    for spec in registered():
        register(f"adapter:{spec.name}", _adapter_check(spec.name), critical=False)


def _adapter_check(name: str) -> HealthCheck:
    async def check() -> Status:
        return health_states().get(name, "ok")

    return check


load_adapters()
load_apis()
load_events()
load_workflows()
