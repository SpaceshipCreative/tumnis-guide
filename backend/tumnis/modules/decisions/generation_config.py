"""The Generation slot's process-wide wiring (P1-03): the deployment's settings and the one
provider instance `generation_api` asks.

The worker calls `decisions.api.configure_generation(settings.generation, net_policy=...)`
once at start. The provider is built on first use through the adapter registry (so the
api process never imports the vLLM adapter): the fake under `TUMNIS_ADAPTERS=fake`, the
real `VllmGeneration` when an endpoint and a model are set, otherwise none, and a caller
gets `None` (the first action stays pending). Tests pass a provider directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from tumnis.core.net import NetPolicy
from tumnis.modules.decisions.adapters.port import GenerationProvider
from tumnis.settings import GenerationSettings


@dataclass
class _Slot:
    settings: GenerationSettings
    net_policy: NetPolicy
    provider: GenerationProvider | None = None
    built: bool = False


_slot = _Slot(GenerationSettings(), NetPolicy(mode="self-hosted"))


def configure(
    settings: GenerationSettings,
    *,
    net_policy: NetPolicy | None = None,
    provider: GenerationProvider | None = None,
) -> None:
    """Replace the slot's settings; `provider` (tests) is used as is, otherwise the next
    call builds one from the settings."""
    global _slot  # noqa: PLW0603  # one Generation slot per process
    _slot = _Slot(
        settings,
        net_policy or NetPolicy(mode="self-hosted"),
        provider,
        built=provider is not None,
    )


def settings() -> GenerationSettings:
    return _slot.settings


def provider() -> GenerationProvider | None:
    """The configured provider; None when the slot has no endpoint."""
    return _slot.provider
