"""The seed set shows one project per health state (P0-17, FR-1.1). Green once tasks
exist and register their stats source (P0-18)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tumnis.core.clock import FixedClock
    from tumnis.seed import SeedResult

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-1.1")
@pytest.mark.wp("P0-17")
@pytest.mark.xfail(strict=True, reason="spec:P0-18")
async def test_seed_projects_show_expected_health(
    db: DbUrls, seed: SeedResult, clock: FixedClock
) -> None:
    """T-P0-17-20
    After `tumnis seed`, each seed project's `health` matches
    `backend/fixtures/seed/expected_health.yaml` (one blocked, one at risk, one on track).
    """
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415
    from tumnis.modules.projects import api  # noqa: PLC0415
    from tumnis.seed import FIXTURES  # noqa: PLC0415

    expected = yaml.safe_load((FIXTURES / "seed" / "expected_health.yaml").read_text())
    assert sorted(expected.values()) == ["at_risk", "blocked", "on_track"]
    ctx = WorkspaceContext(seed.ids["ws_main"], SYSTEM_ACTOR)
    actual = {}
    async with tenant_session(ctx) as s:
        for key in expected:
            project = await api.get_project(s, seed.ids[key], now=clock.now())
            actual[key] = str(project.health)
    assert actual == expected
