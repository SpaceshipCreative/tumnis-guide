"""archived_blobs (P2-18, FR-5.10): a blob name already taken with other bytes is refused
(`BlobCorrupt`) before a caller deletes the live rows it thinks the blob holds; the same
bytes again (a replayed step) are a no-op."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P2-18")
async def test_put_blob_refuses_other_bytes_under_a_taken_name(
    app: FastAPI, workspace: WorkspaceHandle
) -> None:
    from tumnis.core import archive_blobs as blobs  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    project_id = uuid.uuid4()
    async with tenant_session(workspace.ctx) as s:
        for raw in (b"first", b"first"):  # the second: a replayed step, kept as it is
            await blobs.put_blob(
                s, module="agents", kind="run_events", project_id=project_id, ref="b1", raw=raw
            )
        with pytest.raises(blobs.BlobCorrupt):
            await blobs.put_blob(
                s, module="agents", kind="run_events", project_id=project_id, ref="b1", raw=b"x"
            )
        [(_ref, held)] = await blobs.blobs(
            s, module="agents", kind="run_events", project_id=project_id
        )
    assert held == b"first"
