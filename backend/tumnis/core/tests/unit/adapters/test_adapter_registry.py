"""Registry bookkeeping for adapters: contract classes per adapter and adapter health (P0-09)."""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest

if TYPE_CHECKING:
    from tests.fixtures import Fakes
    from tumnis.core.clock import FixedClock

BACKEND = Path(__file__).resolve().parents[5]
CONTRACT_SUITES = (
    "tumnis/modules/*/tests/contract/test_*.py",
    "tumnis/core/tests/contract/test_*.py",
)


def _import_contract_suites() -> None:
    """Import every contract suite so its Test* classes record themselves."""
    for pattern in CONTRACT_SUITES:
        for path in sorted(BACKEND.glob(pattern)):
            dotted = ".".join(path.relative_to(BACKEND).with_suffix("").parts)
            importlib.import_module(dotted)


class Ping(Protocol):
    async def ping(self) -> str: ...


@pytest.mark.req("PRD Testability NFR")
@pytest.mark.wp("P0-09")
@pytest.mark.xfail(strict=True, reason="spec:P0-09")
def test_every_adapter_has_fake_and_real_contract_classes(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-P0-09-13
    For every registered adapter, contract_impls()[name] contains "fake" and one of "real" or
    "recorded"; a contract class naming an unregistered adapter, or an adapter without
    contract classes, is a violation.
    """
    import tumnis.wiring  # noqa: F401, PLC0415
    from tumnis.core.adapters import registry  # noqa: PLC0415
    from tumnis.core.adapters.contract import AdapterContract, contract_impls  # noqa: PLC0415

    _import_contract_suites()
    for spec in registry.registered():
        impls = contract_impls().get(spec.name, set())
        assert "fake" in impls, spec.name
        assert impls & {"real", "recorded"}, spec.name
    assert registry.contract_violations() == []

    # The check bites: an orphan contract class and an adapter without a suite.
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    monkeypatch.setattr(
        registry, "_CONTRACTS", {name: set(impls) for name, impls in contract_impls().items()}
    )

    class OrphanContract(AdapterContract[Ping]):
        port = Ping
        adapter_name = "demo.unregistered"

    class TestOrphanFake(OrphanContract):
        impl = "fake"

    registry.register_adapter("demo.no_suite", port=Ping, real=object, fake=object)
    violations = registry.contract_violations()
    assert any("demo.unregistered" in v for v in violations), violations
    assert any("demo.no_suite" in v for v in violations), violations


@pytest.mark.req("Architecture principle 5")
@pytest.mark.wp("P0-09")
@pytest.mark.xfail(strict=True, reason="spec:P0-09")
async def test_open_breaker_reports_degraded_health(
    clock: FixedClock, fakes: Fakes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-P0-09-14
    health_state() is "degraded" while the breaker is open; wiring registers it as a
    non-critical readiness check "adapter:<name>"; recovery reports "ok" again.
    """
    from tumnis import wiring  # noqa: PLC0415
    from tumnis.core.adapters import registry  # noqa: PLC0415
    from tumnis.core.adapters.base import (  # noqa: PLC0415
        Adapter,
        AdapterUnavailable,
        CallPolicy,
    )
    from tumnis.core.adapters.breaker import BreakerConfig  # noqa: PLC0415
    from tumnis.core.adapters.retry import RetryPolicy  # noqa: PLC0415

    class FlakyPing(Adapter):
        name = "demo.flaky"

        async def ping(self, *, up: bool) -> str:
            async def go() -> str:
                if not up:
                    raise AdapterUnavailable(self.name, "ping", "refused", retryable=True)
                return "pong"

            return await self.call("ping", go, idempotent=True)

    class FakePing:
        async def ping(self) -> str:
            return "pong"

    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    registry.register_adapter("demo.flaky", port=Ping, real=FlakyPing, fake=FakePing)
    adapter = FlakyPing(
        policy=CallPolicy(
            timeout_s=1.0,
            retry=RetryPolicy(max_attempts=1),
            breaker=BreakerConfig(failure_threshold=1, cooldown_s=30.0),
        ),
        clock=clock,
    )

    checks: dict[str, tuple[Callable[[], Awaitable[str]], bool]] = {}

    def register_health(name: str, check: Callable[[], Awaitable[str]], *, critical: bool) -> None:
        checks[name] = (check, critical)

    wiring.register_adapter_health(register_health)
    assert set(checks) == {f"adapter:{spec.name}" for spec in registry.registered()}
    check, critical = checks["adapter:demo.flaky"]
    assert critical is False
    assert adapter.health_state() == "ok"
    assert await check() == "ok"

    with pytest.raises(AdapterUnavailable):
        await adapter.ping(up=False)
    assert adapter.health_state() == "degraded"
    assert registry.health_states()["demo.flaky"] == "degraded"
    assert fakes.adapter_health()["demo.flaky"] == "degraded"
    assert await check() == "degraded"

    clock.advance(seconds=30)
    assert await adapter.ping(up=True) == "pong"
    assert adapter.health_state() == "ok"
    assert await check() == "ok"
