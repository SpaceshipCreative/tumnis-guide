"""The scripted connector passes the shared connector contract over its recordings
(P0-12, FR-14.5). A new connector's contract test looks exactly like this: a recordings
folder under `tests/recordings/<provider>/` and one class per implementation."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.integrations.connector_contract import ConnectorContract

if TYPE_CHECKING:
    from tests.fixtures import Fakes
    from tumnis.modules.integrations.api import Connector

RECORDINGS = Path(__file__).resolve().parents[1] / "recordings" / "scripted"


@pytest.mark.contract
@pytest.mark.req("FR-14.5")
@pytest.mark.wp("P0-12")
@pytest.mark.xfail(strict=True, reason="spec:P0-12")
class TestScriptedConnector(ConnectorContract):
    """T-P0-12-06
    The fake connector passes the shared connector contract over its recordings folder.
    """

    impl = "fake"
    provider = "scripted"
    recordings_dir = RECORDINGS

    @pytest.fixture
    def subject(self, fakes: Fakes) -> Connector:
        connector: Connector = fakes["integrations.connector.scripted"]
        return connector


@pytest.mark.contract
@pytest.mark.req("FR-14.5")
@pytest.mark.wp("P0-12")
@pytest.mark.xfail(strict=True, reason="spec:P0-12")
class TestScriptedConnectorRecorded(ConnectorContract):
    """The registered real side of the scripted connector (a demo with no outside
    dependency, so it is the same class) on the same recordings."""

    impl = "recorded"
    provider = "scripted"
    recordings_dir = RECORDINGS

    @pytest.fixture
    def subject(self) -> Connector:
        import tumnis.wiring  # noqa: F401, PLC0415
        from tumnis.core.adapters.registry import resolve  # noqa: PLC0415

        connector: Connector = resolve("integrations.connector.scripted", "real")
        return connector
