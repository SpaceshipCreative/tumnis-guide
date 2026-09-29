"""The fakes switch: every outside dependency registers a real and a fake factory here.

`TUMNIS_ADAPTERS=fake` (required in preview, REL-7) makes `resolve` hand out fakes. P0-09
adds the adapter base class, timeouts, breaker and the contract-suite base.
"""

import os
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

AdapterMode = Literal["real", "fake"]
MODES: tuple[AdapterMode, ...] = ("real", "fake")
ENV_VAR = "TUMNIS_ADAPTERS"


@dataclass(frozen=True)
class AdapterSpec:
    name: str  # "decisions.jev", "calendar.google"
    port: type[Any]  # the Protocol the adapter implements
    real: Callable[..., Any]
    fake: Callable[..., Any] | None


_REGISTRY: dict[str, AdapterSpec] = {}


def register_adapter(
    name: str,
    *,
    port: type[Any],
    real: Callable[..., Any],
    fake: Callable[..., Any] | None,
) -> None:
    if name in _REGISTRY:
        raise ValueError(f"adapter {name!r} is already registered")
    _REGISTRY[name] = AdapterSpec(name=name, port=port, real=real, fake=fake)


def current_mode() -> AdapterMode:
    """The mode `TUMNIS_ADAPTERS` selects; `real` when unset (A11)."""
    value = os.environ.get(ENV_VAR, "real")
    if value not in MODES:
        raise ValueError(f"{ENV_VAR} must be one of {MODES}, got {value!r}")
    return value


def resolve(name: str, mode: AdapterMode, **deps: Any) -> Any:
    """Build adapter `name` in `mode`; KeyError on an unknown name."""
    spec = _REGISTRY[name]
    factory = spec.real if mode == "real" else spec.fake
    if factory is None:
        raise LookupError(f"adapter {name!r} has no fake")
    return factory(**deps)


def registered() -> tuple[AdapterSpec, ...]:
    return tuple(_REGISTRY.values())


# --- Contract bookkeeping (P0-09) -------------------------------------------------------

_CONTRACTS: dict[str, set[str]] = {}


def record_contract(name: str, impl: str) -> None:
    """Called by each `Test*` subclass of `AdapterContract` as its module is imported."""
    _CONTRACTS.setdefault(name, set()).add(impl)


def contract_impls() -> Mapping[str, set[str]]:
    """Adapter name -> the implementations ("fake", "real", "recorded") with a contract class."""
    return {name: set(impls) for name, impls in _CONTRACTS.items()}


def contract_violations() -> list[str]:
    raise NotImplementedError


# --- Health (P0-09) ----------------------------------------------------------------------


def track(name: str, instance: object) -> None:
    raise NotImplementedError


def health_states() -> dict[str, Literal["ok", "degraded"]]:
    raise NotImplementedError


def validate() -> list[str]:
    """Human-readable violations, empty when valid.

    Every spec has a fake; the fake is a class or factory whose product satisfies `port`.
    """
    violations: list[str] = []
    for spec in _REGISTRY.values():
        if spec.fake is None:
            violations.append(f"{spec.name}: no fake registered")
            continue
        product = _product_type(spec.fake)
        if product is None:
            violations.append(
                f"{spec.name}: the fake factory has no return annotation to check against "
                f"{spec.port.__qualname__}"
            )
            continue
        missing = _missing_members(product, spec.port)
        if missing:
            violations.append(
                f"{spec.name}: fake {product.__qualname__} does not satisfy "
                f"{spec.port.__qualname__} (missing {', '.join(missing)})"
            )
    return violations


def _product_type(factory: Callable[..., Any]) -> type[Any] | None:
    if isinstance(factory, type):
        return factory
    try:
        product = typing.get_type_hints(factory).get("return")
    except NameError:
        return None
    return product if isinstance(product, type) else None


def _missing_members(product: type[Any], port: type[Any]) -> list[str]:
    if getattr(port, "_is_protocol", False):
        return sorted(m for m in typing.get_protocol_members(port) if not hasattr(product, m))
    return [] if issubclass(product, port) else [f"subclass of {port.__qualname__}"]
