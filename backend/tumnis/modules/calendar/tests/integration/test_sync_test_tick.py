"""The `calendar-sync` test tick (R-37, fakes only; A1.3): `POST /v1/test/tick/calendar-sync`
syncs every connected Google account now and answers once the syncs end, so a journey reads
the scripted scenario's events in its free blocks right after it."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.calendar.tests.integration._calendar import T0, connect, rows

if TYPE_CHECKING:
    from dbos import DBOS, DBOSClient

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.modules.calendar.adapters.fake import FakeGoogleCalendar

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.fixture
def stored_scripts() -> Iterator[None]:
    """Stored fake scripts on, as the api and the worker enable them with fakes."""
    from tumnis.core import fake_scripts  # noqa: PLC0415

    fake_scripts.enable()
    try:
        yield
    finally:
        fake_scripts.disable()


@pytest.mark.req("REL-7")
@pytest.mark.wp("P1-09")
async def test_tick_syncs_the_scripted_scenario_for_every_account(  # noqa: PLR0917
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    google: FakeGoogleCalendar,
    oauth_client: None,
    stored_scripts: None,
    dbos: type[DBOS],
    dbos_client: DBOSClient,
) -> None:
    from tumnis.core import fake_scripts  # noqa: PLC0415
    from tumnis.core.ticks import tick  # noqa: PLC0415
    from tumnis.modules.calendar import testing  # noqa: PLC0415
    from tumnis.modules.calendar.adapters.fake import parse_calendar_script  # noqa: PLC0415

    conn_a = await connect(workspace.ctx, "a")
    conn_b = await connect(workspace.ctx, "b")
    key, script = parse_calendar_script({"scenario": "no_ninety_minute_gap"})
    await fake_scripts.put("calendar.google", key, script)

    calendar_sync = tick(testing.TICK_NAME)
    assert calendar_sync is testing.tick
    assert await calendar_sync(dbos_client, T0) == 2

    # The syncs have ended when the tick answers: account a holds the scenario's three
    # Monday events and none of its recordings, account b (an empty answer) none.
    titles = sorted(row["title"] for row in rows(app_db, "events", conn_a))
    assert titles == ["Acme workshop", "Design sync", "Quarterly review"]
    assert rows(app_db, "events", conn_b) == []
