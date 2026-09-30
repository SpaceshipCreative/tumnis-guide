"""decisions adapters: one file per outside dependency, each with a fake.

`decisions.jev` is registered with a real factory that imports `adapters/jev.py` (and so
`typesafe_sdk`) only when a real provider is built, which happens in the worker: the api
process wires the registry but never imports the SDK (import-linter `api-never-calls-out`).
`decisions.vllm`, the fallback (P1-02), is built the same lazy way; its fake is a second
`FakeDecisions`. `decisions.vllm_generation` (P1-03) too: only `generation_api` asks it
(import-linter `generation-callers`), and only the worker makes its outbound call.
"""

import importlib
from typing import Any

from tumnis.core.adapters.registry import register_adapter
from tumnis.core.fake_scripts import register_fake_script
from tumnis.modules.decisions.adapters.fake import (
    GENERATION_HOOK,
    FakeDecisions,
    FakeGeneration,
    fake_vllm,
    parse_decisions_script,
    parse_generation_script,
)
from tumnis.modules.decisions.adapters.port import DecisionsProvider, GenerationProvider

JEV_MODULE = "tumnis.modules.decisions.adapters.jev"
VLLM_MODULE = "tumnis.modules.decisions.adapters.vllm"
VLLM_GENERATION_MODULE = "tumnis.modules.decisions.adapters.vllm_generation"


def build_jev(**deps: Any) -> DecisionsProvider:
    """JevDecisions(api_key, pinned_model, clock=...), imported on first use."""
    jev = importlib.import_module(JEV_MODULE)
    provider: DecisionsProvider = jev.JevDecisions(**deps)
    return provider


def build_vllm(**deps: Any) -> DecisionsProvider:
    """VllmDecisions(base_url, clock=..., net_policy=...), imported on first use."""
    vllm = importlib.import_module(VLLM_MODULE)
    provider: DecisionsProvider = vllm.VllmDecisions(**deps)
    return provider


def build_vllm_generation(**deps: Any) -> GenerationProvider:
    """VllmGeneration(base_url, model, clock=..., net_policy=...), imported on first use."""
    vllm = importlib.import_module(VLLM_GENERATION_MODULE)
    provider: GenerationProvider = vllm.VllmGeneration(**deps)
    return provider


register_adapter("decisions.jev", port=DecisionsProvider, real=build_jev, fake=FakeDecisions)
register_adapter("decisions.vllm", port=DecisionsProvider, real=build_vllm, fake=fake_vllm)
register_adapter(
    "decisions.vllm_generation",
    port=GenerationProvider,
    real=build_vllm_generation,
    fake=FakeGeneration,
)

# Scriptable across processes through POST /v1/test/fakes/{hook}/script (R-37).
register_fake_script("decisions.jev", parse_decisions_script)
register_fake_script("decisions.vllm", parse_decisions_script)
register_fake_script(GENERATION_HOOK, parse_generation_script)
