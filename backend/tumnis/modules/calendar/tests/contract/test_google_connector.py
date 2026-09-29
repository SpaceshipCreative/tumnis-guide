"""The Google Calendar connector passes the shared connector contract over its recordings
(P1-09, FR-14.5): `map` is pure and gives the expected records, `sync` pages end.

`TestGoogleCalendarOnRecordings` runs the registered real connector on the real Google
client, whose HTTP goes to a replay of the recorded responses (no socket is opened; the
contract layer disables them). `TestGoogleCalendarFake` runs the registered fake."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.integrations.connector_contract import ConnectorContract

if TYPE_CHECKING:
    from tests.fixtures import Fakes
    from tumnis.modules.integrations.api import Connector

RECORDINGS = Path(__file__).resolve().parents[1] / "recordings" / "google_calendar"


@pytest.mark.contract
@pytest.mark.req("FR-14.5")
@pytest.mark.wp("P1-09")
@pytest.mark.xfail(strict=True, reason="spec:P1-09")
class TestGoogleCalendarOnRecordings(ConnectorContract):
    """T-P1-09-01
    The real connector (real Google client over recorded HTTP responses) passes the P0-12
    connector contract; `map` is pure.
    """

    impl = "recorded"
    provider = "google_calendar"
    recordings_dir = RECORDINGS

    @pytest.fixture
    def subject(self) -> Connector:
        from tumnis.modules.calendar.tests.replay import recorded_connector  # noqa: PLC0415

        return recorded_connector()


@pytest.mark.contract
@pytest.mark.req("FR-14.5")
@pytest.mark.wp("P1-09")
@pytest.mark.xfail(strict=True, reason="spec:P1-09")
class TestGoogleCalendarFake(ConnectorContract):
    """The registered fake connector (recording replay) passes the same contract."""

    impl = "fake"
    provider = "google_calendar"
    recordings_dir = RECORDINGS

    @pytest.fixture
    def subject(self, fakes: Fakes) -> Connector:
        connector: Connector = fakes["integrations.connector.google_calendar"]
        return connector
