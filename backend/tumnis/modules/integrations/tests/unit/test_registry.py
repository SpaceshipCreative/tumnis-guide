"""Connectors register in the adapter registry, fake included (P0-12, FR-14.5)."""

from __future__ import annotations

import pytest


@pytest.mark.req("FR-14.5")
@pytest.mark.wp("P0-12")
def test_connector_registers_in_adapter_registry_with_fake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P0-12-09
    register_connector without a fake raises and registers nothing; with one, the adapter
    `integrations.connector.<provider>` is registered with the Connector port, fake mode
    resolves to the fake and real mode to the real, and the connector index knows its kind.
    """
    from tumnis.core.adapters import registry  # noqa: PLC0415
    from tumnis.modules.integrations import api  # noqa: PLC0415
    from tumnis.modules.integrations.adapters.fake import ScriptedConnector  # noqa: PLC0415

    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    monkeypatch.setattr(api, "_CONNECTORS", dict(api._CONNECTORS))

    class DemoReal(ScriptedConnector):
        pass

    with pytest.raises(api.ConnectorWithoutFake):
        api.register_connector("demo", "notes", real=DemoReal)
    with pytest.raises(api.ConnectorWithoutFake):
        api.register_connector("demo", "notes", real=DemoReal, fake=None)
    assert "integrations.connector.demo" not in {spec.name for spec in registry.registered()}
    assert "demo" not in api.connectors()

    api.register_connector("demo", "notes", real=DemoReal, fake=ScriptedConnector)
    spec = next(s for s in registry.registered() if s.name == "integrations.connector.demo")
    assert spec.port is api.Connector
    assert api.connectors()["demo"].kind == "notes"
    assert api.connectors()["demo"].adapter_name == "integrations.connector.demo"
    assert registry.validate() == []

    monkeypatch.setenv("TUMNIS_ADAPTERS", "fake")
    assert type(api.connector_for("demo")) is ScriptedConnector
    monkeypatch.setenv("TUMNIS_ADAPTERS", "real")
    assert type(api.connector_for("demo")) is DemoReal

    with pytest.raises(ValueError, match="already registered"):
        api.register_connector("demo", "notes", real=DemoReal, fake=ScriptedConnector)
