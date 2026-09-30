"""The folder-sync fixture set (P1-15, FR-15.12): every scenario in
`backend/fixtures/folder_sync/` runs end to end on a server path and, where it says so, on
MinIO. The scenario format and the runner's operations are in `_folder_runner.py`."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.knowledge.tests.integration._folder_runner import (
    FolderRunner,
    load_scenarios,
    make_root,
    remove_share,
)

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

SCENARIOS = Path(__file__).resolve().parents[5] / "fixtures" / "folder_sync"
CASES = load_scenarios(SCENARIOS)


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-15")
@pytest.mark.parametrize(
    ("backend", "scenario"),
    [pytest.param(backend, scenario, id=f"{name}-{backend}") for name, backend, scenario in CASES],
)
async def test_scenario(  # noqa: PLR0917  # fixtures
    request: pytest.FixtureRequest,
    db: DbUrls,
    dbos: type[DBOS],
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
    backend: str,
    scenario: dict[str, Any],
) -> None:
    """T-P1-15-08
    Each folder-sync scenario (outside edits, touches, renames, deletes, conflicts, a taken
    conflict name, sanitized upload names, a dropped mount, an S3 ETag change) passes on
    the backends it names; the clock is fixed at 2026-03-09.
    """
    runner = FolderRunner(db=db, ws=knowledge_ws, clock=clock, backend=backend)
    if backend == "server_path":
        runner.root = make_root(tmp_path / "share")
    else:
        runner.minio = request.getfixturevalue("minio")
    try:
        await runner.start()
        await runner.run(scenario["steps"])
    finally:
        await runner.close()
        if runner.root is not None:
            remove_share(runner.root)


P3_14_CASES = [
    (name, backend, scenario)
    for name, on, scenario in CASES
    if on == "server_path"
    for backend in ("share", "sftp")
]


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P3-14")
@pytest.mark.xfail(strict=True, reason="spec:P3-14")
@pytest.mark.parametrize(
    ("backend", "scenario"),
    [pytest.param(b, scenario, id=f"{name}-{b}") for name, b, scenario in P3_14_CASES],
)
async def test_folder_sync_set_on_sftp_and_share(  # noqa: PLR0917  # fixtures
    request: pytest.FixtureRequest,
    db: DbUrls,
    dbos: type[DBOS],
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
    backend: str,
    scenario: dict[str, Any],
) -> None:
    """T-P3-14-17
    P1-15's folder-sync fixture set (outside edits, touches, renames, deletes, conflicts, a
    dropped mount) passes on a share and on the SFTP container: every scenario that runs on
    a server path runs on both new backends, unchanged. On SFTP a dropped mount is the
    location's root going away.
    """
    runner = FolderRunner(db=db, ws=knowledge_ws, clock=clock, backend=backend)
    if backend == "share":
        runner.root = make_root(tmp_path / "share")
    else:
        runner.sftp = request.getfixturevalue("sftp_server")
    try:
        await runner.start()
        await runner.run(scenario["steps"])
    finally:
        await runner.close()
        if runner.root is not None:
            remove_share(runner.root)
