"""The move route's check before it queues a move (P3-14 review follow-up, FR-15.12): a
target that cannot take the folder is refused with a problem (409, or 422 for an unsafe
path) instead of a 202 for a move that would never start; missing rows are 404 first."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.knowledge.tests.integration._folder_runner import make_root
from tumnis.modules.knowledge.tests.integration._locations import SELF_HOSTED
from tumnis.modules.knowledge.tests.integration.test_locations import (
    _project,
    _server_path_location,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P3-14")
async def test_move_precheck_refuses_before_queueing(
    knowledge_ws: WorkspaceHandle, clock: FixedClock, tmp_path: Path
) -> None:
    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.core.versioning import NotFound  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge import move  # noqa: PLC0415

    ws = knowledge_ws
    source = await _server_path_location(ws, make_root(tmp_path / "a"), "a")
    target = await _server_path_location(ws, make_root(tmp_path / "b"), "b", default=False)
    project_id = await _project(ws, clock)
    other_id = await _project(ws, clock, name="Other")
    async with tenant_session(ws.ctx) as s:
        folder = await knowledge.ensure_project_folder(s, project_id, net=SELF_HOSTED)
        assert folder is not None
        await knowledge.set_project_location(s, other_id, target, net=SELF_HOSTED)
        other = await knowledge.get_project_folder(s, other_id)

    async def refused(location: uuid.UUID, path: str) -> tuple[int, str]:
        async with tenant_session(ws.ctx) as s:
            with pytest.raises(ProblemError) as caught:
                await move.precheck(s, project_id, location, path)
        return caught.value.status, caught.value.code

    assert await refused(source, folder.root_path) == (409, "same_folder")
    assert await refused(target, other.root_path) == (409, "folder_taken")
    assert await refused(target, "../outside") == (422, "path_rejected")
    with pytest.raises(NotFound):
        async with tenant_session(ws.ctx) as s:
            await move.precheck(s, project_id, uuid.uuid4(), "elsewhere")

    async with tenant_session(ws.ctx) as s:  # a free folder on an online location passes
        await move.precheck(s, project_id, target, "moved/acme")
