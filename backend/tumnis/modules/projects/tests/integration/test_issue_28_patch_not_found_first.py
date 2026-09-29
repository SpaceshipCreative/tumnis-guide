"""Issue #28: PATCH on a project the caller cannot see answers 404 before any body rule
(A0.3 requires 404 for every write aimed at another workspace's row)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("ADR-0009")
@pytest.mark.wp("P0-17")
@pytest.mark.parametrize("patch", [{"name": None}, {"status": None}, {"code_path": "x"}])
async def test_issue_28_patch_of_unseen_project_is_not_found_before_validation(
    app: FastAPI, workspace: WorkspaceHandle, patch: dict[str, object]
) -> None:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.core.versioning import NotFound  # noqa: PLC0415
    from tumnis.modules.projects import api  # noqa: PLC0415

    body = api.ProjectPatch.model_validate({**patch, "version": 1})
    async with tenant_session(workspace.ctx) as s:
        with pytest.raises(NotFound):
            await api.update_project(s, workspace.ctx.actor, uuid.uuid4(), body, 1)
