"""A proposal's taint on its create path (P2-08, SAF-1): the OR of its context items'
stored taint. P3-07's `create_proposal` writes it; until the proposals table lands, the
rule is `integrations.api.proposal_taint`."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-08")
async def test_proposal_taint_is_the_or_of_its_items(
    workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """Clean items give an untainted proposal; one tainted item among them taints it; an
    id that is no live item of the workspace is NotFound."""
    from tests._mcp import make_world  # noqa: PLC0415
    from tests._taint import context_item  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415
    from tumnis.core.versioning import NotFound  # noqa: PLC0415
    from tumnis.modules.integrations import api as integrations  # noqa: PLC0415

    world = await make_world(workspace, clock)
    ctx = WorkspaceContext(workspace.id, ActorRef(f"user:{workspace.user_id}"))
    project = world.projects["A"]
    clean = [await context_item(ctx, project, tainted=False) for _ in range(2)]
    dirty = await context_item(ctx, project, tainted=True)

    async with tenant_session(ctx) as s:
        assert await integrations.proposal_taint(s, clean) is False
        assert await integrations.proposal_taint(s, [*clean, dirty]) is True
        assert await integrations.proposal_taint(s, [dirty]) is True
        with pytest.raises(NotFound):
            await integrations.proposal_taint(s, [*clean, uuid.uuid4()])
