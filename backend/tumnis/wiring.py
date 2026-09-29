"""Composition root: imports every module's adapters so they register (later: routers,
subscribers). Driven by the module registry, so a new module is wired automatically."""

import importlib
from collections.abc import Awaitable, Callable
from typing import Protocol

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
    raise NotImplementedError


load_adapters()
