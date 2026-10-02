"""A move checks the file size limit before it copies (P3-14 follow-up, FR-15.12, SEC-10).
A file over the 50 MiB limit cannot be written to the target, so a move used to copy the
batches before it, then fail on that file with its steps' retries spent (`copy_failed`).
It now fails `too_large` before copying anything."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER
from tumnis.modules.knowledge.tests.integration._folder_runner import make_root
from tumnis.modules.knowledge.tests.integration.test_locations import _server_path_location
from tumnis.modules.knowledge.tests.integration.test_move import _project_with_files, _tree

if TYPE_CHECKING:
    from pathlib import Path

    from dbos import DBOS

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _rows(db: DbUrls, sql: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(sql, params).fetchall()


@pytest.mark.req("FR-15.12", "SEC-10")
@pytest.mark.wp("P3-14")
async def test_move_with_a_file_over_the_limit_fails_before_copying(  # noqa: PLR0917  # fixtures
    db: DbUrls,
    dbos: type[DBOS],
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source file over the size limit (lowered here to 20 bytes, so the 26-byte
    outside PDF is over it) ends the move `failed` (`too_large`) with nothing copied to the
    target and nothing switched."""
    from dbos import SetWorkflowID  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge import move, workflows  # noqa: PLC0415

    ws = knowledge_ws
    source, project_id, folder = await _project_with_files(ws, clock, make_root(tmp_path / "a"))
    target_root = make_root(tmp_path / "target")
    target = await _server_path_location(ws, target_root, "target", default=False)
    before = _tree(target_root)  # the location's marker file only
    monkeypatch.setattr(move, "MAX_FILE_BYTES", 20)

    with SetWorkflowID(f"move-{uuid.uuid4()}"):
        result = await workflows.move_project_folder(
            str(ws.id), str(project_id), str(target), "acme-moved"
        )

    assert (result["status"], result["reason"]) == ("failed", "too_large"), result
    assert _tree(target_root) == before
    assert not (target_root / "acme-moved").exists()
    async with tenant_session(ws.ctx) as s:
        now = await knowledge.get_project_folder(s, project_id)
    assert (now.location_id, now.root_path) == (source, folder)
    assert _rows(
        db, "SELECT status, reason FROM folder_moves WHERE project_id = %s", project_id
    ) == [("failed", "too_large")]
