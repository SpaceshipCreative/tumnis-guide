"""Cache invalidation reaches every process through LISTEN/NOTIFY, and only on commit
(P0-08, Caching)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import pytest

from tests._pg import APP
from tumnis.core.tests.integration._probe import cache_probe

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

GONE_WITHIN_MS = 1_000


class RollbackError(Exception):
    """Raised inside a transaction to roll it back."""


async def _invalidate_then_roll_back(ctx: Any, key: Any) -> None:
    from tumnis.core.cache import invalidate_on_commit  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    async with tenant_session(ctx) as session:
        await invalidate_on_commit(session, key)
        raise RollbackError


@pytest.fixture
async def core_db(db: DbUrls) -> AsyncIterator[None]:
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    try:
        yield
    finally:
        await core_db.dispose()


@pytest.mark.req("Caching")
@pytest.mark.wp("P0-08")
@pytest.mark.xfail(strict=True, reason="spec:P0-08")
@pytest.mark.usefixtures("core_db")
async def test_invalidation_reaches_second_process_within_one_second(
    db: DbUrls, workspace: WorkspaceHandle
) -> None:
    """T-P0-08-05
    Given a probe process that cached ws:<A>:settings:x, when the parent commits a
    transaction calling invalidate_on_commit(key), then the probe prints `gone` less than
    1,000 ms after the commit.
    """
    from tumnis.core.cache import CacheKey, invalidate_on_commit  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    key = CacheKey.for_workspace(workspace.id, "settings", "x")
    async with cache_probe(db.libpq(APP), key.value) as probe:
        async with tenant_session(workspace.ctx) as session:
            await invalidate_on_commit(session, key)
        committed = time.monotonic()
        line = await probe.line(wait_s=5.0)
        elapsed_ms = (time.monotonic() - committed) * 1000

    assert line is not None
    assert line.startswith("gone"), line
    assert elapsed_ms < GONE_WITHIN_MS, elapsed_ms


@pytest.mark.req("Caching")
@pytest.mark.wp("P0-08")
@pytest.mark.xfail(strict=True, reason="spec:P0-08")
@pytest.mark.usefixtures("core_db")
async def test_rolled_back_write_does_not_invalidate(
    db: DbUrls, workspace: WorkspaceHandle
) -> None:
    """T-P0-08-06
    An invalidation inside a rolled-back transaction leaves the other process's entry and
    this process's own entry; a committed one afterwards still reaches the probe.
    """
    from tumnis.core.cache import (  # noqa: PLC0415
        CacheKey,
        InProcessCache,
        invalidate_on_commit,
        use_backend,
    )
    from tumnis.core.clock import SystemClock  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    key = CacheKey.for_workspace(workspace.id, "settings", "x")
    local = InProcessCache(SystemClock())
    with use_backend(local):
        await local.set(key, b"cached")
        async with cache_probe(db.libpq(APP), key.value) as probe:
            with pytest.raises(RollbackError):
                await _invalidate_then_roll_back(workspace.ctx, key)
            assert await probe.line(wait_s=1.5) is None
            assert await local.get(key) == b"cached"

            async with tenant_session(workspace.ctx) as session:
                await invalidate_on_commit(session, key)
            line = await probe.line(wait_s=5.0)
            assert line is not None
            assert line.startswith("gone"), line
            assert await local.get(key) is None
