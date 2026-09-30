"""Fixtures for the knowledge integration tests.

P1-14: `knowledge_ws`, the core database pointed at the test database with a master key
loaded (location credentials are sealed with the workspace data key), and knowledge's
`project.created` subscribers registered.

P1-16: every test gets its own spool and scratch folders (`KNOWLEDGE__SPOOL_DIR` and
`KNOWLEDGE__SCRATCH_DIR` for any app or worker built during the test, and the in-process
extraction pipeline configured with them). `extract_env` adds a project with its folder on
a server-path location (a `FakeStorage` in fakes mode), the pipeline's fakes (scanner,
Docling, vision) and a log of the pipeline steps that ran, in order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
    from pathlib import Path
    from uuid import UUID

    from tests._pg import DbUrls
    from tests.fixtures import Fakes, MasterKeyFile, WorkspaceHandle
    from tumnis.core.clock import FixedClock


@pytest.fixture
def knowledge_ws(
    db: DbUrls, workspace: WorkspaceHandle, master_key_file: MasterKeyFile
) -> WorkspaceHandle:
    import tumnis.modules.knowledge.events  # noqa: F401, PLC0415  # registers the subscribers
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    return workspace


@dataclass(frozen=True)
class ExtractDirs:
    spool: Path
    scratch: Path


@pytest.fixture(autouse=True)
def extract_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ExtractDirs]:
    """The test's own spool and scratch folders, set before any app or worker is built
    (autouse, so it runs before the `app` fixture reads the environment)."""
    from tumnis.modules.knowledge import pipeline  # noqa: PLC0415
    from tumnis.settings import KnowledgeSettings  # noqa: PLC0415

    dirs = ExtractDirs(tmp_path / "spool", tmp_path / "scratch")
    dirs.spool.mkdir()
    dirs.scratch.mkdir()
    monkeypatch.setenv("KNOWLEDGE__SPOOL_DIR", str(dirs.spool))
    monkeypatch.setenv("KNOWLEDGE__SCRATCH_DIR", str(dirs.scratch))
    previous = pipeline.configure(
        KnowledgeSettings(spool_dir=str(dirs.spool), scratch_dir=str(dirs.scratch))
    )
    try:
        yield dirs
    finally:
        pipeline.configure(previous)


@dataclass
class ExtractEnv:
    ws: WorkspaceHandle
    project_id: UUID
    location_id: UUID
    folder: str  # the project folder's root path on the location
    dirs: ExtractDirs
    scanner: Any  # FakeClamAV
    extractor: Any  # FakeDocling
    vision: Any  # FakeVision
    steps: list[str] = field(default_factory=list)


def _logged(name: str, fn: Callable[..., Awaitable[Any]], steps: list[str]) -> Any:
    async def logged(*args: Any, **kw: Any) -> Any:
        steps.append(name)
        return await fn(*args, **kw)

    return logged


def log_steps(monkeypatch: pytest.MonkeyPatch, steps: list[str]) -> None:
    """Append each pipeline step's name to `steps` as it runs (in this process)."""
    from tumnis.modules.knowledge import pipeline  # noqa: PLC0415

    for name in pipeline.STEPS:
        monkeypatch.setattr(pipeline, name, _logged(name, getattr(pipeline, name), steps))


@pytest.fixture
async def extract_env(  # noqa: PLR0917
    db: DbUrls,
    dbos_sys_db: DbUrls,
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    fakes: Fakes,
    extract_dirs: ExtractDirs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[ExtractEnv]:
    """A project in `knowledge_ws` with its folder on the default (server path) location,
    the pipeline on fakes, the api's DBOS client on the test's system database, and the
    step log."""
    from tests._pg import APP  # noqa: PLC0415
    from tumnis.core import deadletter  # noqa: PLC0415
    from tumnis.core.net import NetPolicy  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge import pipeline  # noqa: PLC0415
    from tumnis.modules.knowledge.adapters.fake import FakeDocling, FakeVision  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    ws = knowledge_ws
    root = tmp_path / "location"
    root.mkdir()
    async with tenant_session(ws.ctx) as s:
        location = await knowledge.create_location(
            s,
            knowledge.LocationIn(name="disk", kind="server_path", root=str(root), is_default=True),
            net=NetPolicy(mode="self-hosted"),
        )
        project = await projects.create_project(
            s, ws.ctx.actor, projects.ProjectCreate(name="Acme site"), now=clock.now()
        )
        folder = await knowledge.assign_project_folder(s, project.id)
    assert folder is not None
    env = ExtractEnv(
        ws=ws,
        project_id=project.id,
        location_id=location.id,
        folder=folder.root_path,
        dirs=extract_dirs,
        scanner=fakes["knowledge.clamav"],
        extractor=FakeDocling(),
        vision=FakeVision(),
    )
    previous = pipeline.use(scanner=env.scanner, extractor=env.extractor, vision=env.vision)
    log_steps(monkeypatch, env.steps)
    deadletter.configure(dbos_sys_db.url(APP))
    try:
        yield env
    finally:
        pipeline.use(**previous)
        deadletter.close()
