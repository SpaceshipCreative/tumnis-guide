"""Fixtures for the github integration tests (P2-13): the core database pointed at the
test database, and the recording-replay GitHub fake with the test clock wired into the
github workflows (`github`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tumnis.modules.github.tests.integration import _github

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from tests._pg import DbUrls
    from tests.fixtures import MasterKeyFile
    from tumnis.core.clock import FixedClock
    from tumnis.modules.github.adapters.fake import FakeGitHubStatus


@pytest.fixture
async def app_db(db: DbUrls, master_key_file: MasterKeyFile) -> AsyncIterator[DbUrls]:
    async with _github.configured(db):
        yield db


@pytest.fixture
def github(clock: FixedClock) -> Iterator[FakeGitHubStatus]:
    """The recording-replay GitHub fake, used by the github workflows for this test."""
    with _github.wired(clock) as fake:
        yield fake
