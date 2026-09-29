"""Composition root: imports every module's adapters so they register (later: routers,
subscribers). Driven by the module registry, so a new module is wired automatically."""

import importlib

from tumnis.core.modules import MODULES


def load_adapters() -> None:
    for module in MODULES:
        importlib.import_module(f"tumnis.modules.{module}.adapters")


load_adapters()
