"""Fixtures for the decisions integration tests (P1-02): the core database configured on
the test clone, a process cache on the test clock, and the two decision fakes."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import Fakes
    from tumnis.core.clock import FixedClock


@pytest.fixture
async def core_db(db: DbUrls) -> AsyncIterator[None]:
    """tumnis.core.db on the test's database (app role, no pool)."""
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    try:
        yield
    finally:
        await core_db.dispose()


@pytest.fixture
def test_cache(clock: FixedClock) -> Iterator[Any]:
    """The process cache swapped for one reading the test clock, so TTLs follow it."""
    from tumnis.core.cache import InProcessCache, use_backend  # noqa: PLC0415

    with use_backend(InProcessCache(clock)) as cache:
        yield cache


@pytest.fixture
def providers(fakes: Fakes) -> Any:
    """The Jev fake as the primary and a second fake as the vLLM fallback."""
    from tumnis.modules.decisions.api import Providers  # noqa: PLC0415

    return Providers(jev=fakes["decisions.jev"], vllm=fakes["decisions.vllm"])
