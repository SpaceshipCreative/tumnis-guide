"""Shared setup for the Obsidian vault integration tests (P3-12): the fixture vault copied
to a temporary folder, the Acme and Lab projects, an `obsidian` connection, and readers of
the rows the sync wrote."""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

FIXTURE_VAULT = Path(__file__).parents[1] / "fixtures" / "vault"


def copy_vault(dest: Path) -> Path:
    shutil.copytree(FIXTURE_VAULT, dest)
    return dest


@dataclass
class VaultEnv:
    ws: WorkspaceHandle
    connection_id: UUID
    projects: dict[str, UUID]  # slug -> id
    extractions: list[tuple[UUID, UUID, str]] = field(default_factory=list)

    async def extract(self, workspace_id: UUID, version_id: UUID, path: str) -> None:
        """The attachment extraction hook: records the request instead of enqueueing."""
        self.extractions.append((workspace_id, version_id, path))


async def vault_env(ws: WorkspaceHandle, clock: FixedClock) -> VaultEnv:
    """Projects "Acme" and "Lab" and the workspace's `obsidian` connection."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.integrations import api as integrations  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    made: dict[str, UUID] = {}
    async with tenant_session(ws.ctx) as s:
        for name in ("Acme", "Lab"):
            project = await projects.create_project(
                s, ws.ctx.actor, projects.ProjectCreate(name=name), now=clock.now()
            )
            made[name.lower()] = project.id
        connection_id = await integrations.seed_connection(s, "knowledge", "obsidian", "vault")
    return VaultEnv(ws=ws, connection_id=connection_id, projects=made)


async def vault_documents(env: VaultEnv, *, trashed: bool = False) -> dict[str, dict[str, Any]]:
    """The connection's documents by vault path (live ones, or trashed ones)."""
    from sqlalchemy import select  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge.models import Document  # noqa: PLC0415

    t = Document.__table__
    deleted = t.c.deleted_at.is_not(None) if trashed else t.c.deleted_at.is_(None)
    async with tenant_session(env.ws.ctx) as s:
        rows = (
            (await s.execute(select(t).where(t.c.connection_id == env.connection_id, deleted)))
            .mappings()
            .all()
        )
    return {row["path"]: dict(row) for row in rows}


async def version_numbers(env: VaultEnv, document_id: UUID) -> list[int]:
    from sqlalchemy import select  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge.models import DocumentVersion  # noqa: PLC0415

    t = DocumentVersion.__table__
    async with tenant_session(env.ws.ctx) as s:
        found: Any = await s.scalars(
            select(t.c.version_no).where(t.c.document_id == document_id).order_by(t.c.version_no)
        )
        return list(found)


async def document_links(env: VaultEnv) -> list[dict[str, Any]]:
    from sqlalchemy import select  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    _models = importlib.import_module("tumnis.modules.knowledge.models")
    DocumentLink = _models.DocumentLink  # noqa: N806

    t = DocumentLink.__table__
    async with tenant_session(env.ws.ctx) as s:
        rows = (await s.execute(select(t))).mappings().all()
    return [dict(row) for row in rows]


def git(*args: str, cwd: Path) -> str:
    """Run git for test setup with a clean environment (no GIT_* from a hook or CI) and a
    fixed identity."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="Test",
        GIT_AUTHOR_EMAIL="test@example.com",
        GIT_COMMITTER_NAME="Test",
        GIT_COMMITTER_EMAIL="test@example.com",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
    )
    done = subprocess.run(  # noqa: S603  # fixed git argv, test setup only
        ["git", *args],  # noqa: S607
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout
