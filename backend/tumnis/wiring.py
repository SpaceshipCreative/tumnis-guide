"""Composition root: imports every module's adapters so they register, and registers each
module's optional `api.health()` as a non-critical readiness check (later: routers,
subscribers). Driven by the module registry, so a new module is wired automatically."""

import importlib

from tumnis.core.health import register_health
from tumnis.core.modules import MODULES


def load_adapters() -> None:
    for module in MODULES:
        importlib.import_module(f"tumnis.modules.{module}.adapters")


def register_module_health() -> None:
    """A module that defines `async def health() -> Status` in its api degrades readiness
    when it fails, never takes it down."""
    for module in MODULES:
        api = importlib.import_module(f"tumnis.modules.{module}.api")
        check = getattr(api, "health", None)
        if check is not None:
            register_health(f"module:{module}", check, critical=False)


load_adapters()
