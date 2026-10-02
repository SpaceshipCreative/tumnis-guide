"""The framework's fake source passes P0-12's connector contract (P3-02, FR-14.5): the
connector template is one client, one pure mapper and a folder of recordings, and this is
that template filled in for the `fake` provider."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.integrations.connector_contract import ConnectorContract

if TYPE_CHECKING:
    from tests.fixtures import Fakes
    from tumnis.modules.integrations.api import Connector

RECORDINGS = Path(__file__).resolve().parents[1] / "recordings" / "fake"


@pytest.mark.contract
@pytest.mark.req("FR-14.5")
@pytest.mark.wp("P3-02")
class TestFakeSourceConnector(ConnectorContract):
    """T-P3-02-11
    The fake source (`integrations.connector.fake`, the framework's scriptable connector)
    passes the shared connector contract over its recordings folder.
    """

    impl = "fake"
    provider = "fake"
    recordings_dir = RECORDINGS

    @pytest.fixture
    def subject(self, fakes: Fakes) -> Connector:
        connector: Connector = fakes["integrations.connector.fake"]
        return connector


@pytest.mark.contract
@pytest.mark.req("FR-14.5")
@pytest.mark.wp("P3-02")
class TestFakeSourceRecorded(ConnectorContract):
    """The registered real side of the fake source (it has no outside dependency, so it is
    the same class) on the same recordings."""

    impl = "recorded"
    provider = "fake"
    recordings_dir = RECORDINGS

    @pytest.fixture
    def subject(self) -> Connector:
        import tumnis.wiring  # noqa: F401, PLC0415
        from tumnis.core.adapters.registry import resolve  # noqa: PLC0415

        connector: Connector = resolve("integrations.connector.fake", "real")
        return connector
