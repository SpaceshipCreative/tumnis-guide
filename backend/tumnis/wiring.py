"""Composition root: imports every module's adapters so they register (later: routers,
subscribers). Driven by the module registry, so a new module is wired automatically."""

import importlib
from collections.abc import Awaitable, Callable
from typing import Protocol

from tumnis.core.adapters.registry import health_states, registered
from tumnis.core.modules import MODULES


class RegisterHealth(Protocol):
    """The shape of `tumnis.core.health.register_health` (P0-04)."""

    def __call__(
        self, name: str, check: Callable[[], Awaitable[str]], *, critical: bool
    ) -> None: ...


def load_adapters() -> None:
    for module in MODULES:
        importlib.import_module(f"tumnis.modules.{module}.adapters")


def register_adapter_health(register: RegisterHealth) -> None:
    """Register `adapter:<name>` as a non-critical readiness check for every adapter.

    Called with `tumnis.core.health.register_health` once that exists (P0-04): an open
    breaker degrades readiness, it never takes the service down.
    """
    for spec in registered():
        register(f"adapter:{spec.name}", _adapter_check(spec.name), critical=False)


def _adapter_check(name: str) -> Callable[[], Awaitable[str]]:
    async def check() -> str:
        return health_states().get(name, "ok")

    return check


load_adapters()
