"""decisions adapters: one file per outside dependency, each with a fake.

`decisions.jev` is registered with a real factory that imports `adapters/jev.py` (and so
`typesafe_sdk`) only when a real provider is built, which happens in the worker: the api
process wires the registry but never imports the SDK (import-linter `api-never-calls-out`).
"""

import importlib
from typing import Any

from tumnis.core.adapters.registry import register_adapter
from tumnis.modules.decisions.adapters.fake import FakeDecisions
from tumnis.modules.decisions.adapters.port import DecisionsProvider

JEV_MODULE = "tumnis.modules.decisions.adapters.jev"


def build_jev(**deps: Any) -> DecisionsProvider:
    """JevDecisions(api_key, pinned_model, clock=...), imported on first use."""
    jev = importlib.import_module(JEV_MODULE)
    provider: DecisionsProvider = jev.JevDecisions(**deps)
    return provider


register_adapter("decisions.jev", port=DecisionsProvider, real=build_jev, fake=FakeDecisions)
