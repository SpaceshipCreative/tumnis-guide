"""Fixtures for the calendar integration tests (P1-09): the core database pointed at the
test database, the Google OAuth client set for `workspace`, and the fake Google API and
the test clock wired into the calendar workflows."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tumnis.modules.calendar.tests.integration import _calendar

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from tests._pg import DbUrls
    from tests.fixtures import MasterKeyFile, WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.calendar.adapters.fake import FakeGoogleCalendar


@pytest.fixture
async def app_db(db: DbUrls, master_key_file: MasterKeyFile) -> AsyncIterator[DbUrls]:
    async with _calendar.configured(db):
        yield db


@pytest.fixture
def google(clock: FixedClock) -> Iterator[FakeGoogleCalendar]:
    """The recording-replay Google fake, used by the calendar workflows for this test."""
    with _calendar.wired(clock) as fake:
        yield fake


@pytest.fixture
async def oauth_client(app_db: DbUrls, workspace: WorkspaceHandle) -> None:
    """The workspace's Google OAuth client (the `calendar.google` settings section)."""
    await _calendar.set_oauth_client(workspace.ctx)
