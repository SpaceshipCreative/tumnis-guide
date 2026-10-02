"""The Obsidian Git path against a real git (P3-12, FR-15.10): a local bare repository over
`file://` stands in for the remote."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.knowledge.tests.integration._vault import (
    copy_vault,
    git,
    vault_documents,
    vault_env,
    version_numbers,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
@pytest.mark.xfail(strict=True, reason="spec:P3-12")
async def test_pull_from_local_bare_repo(
    db: DbUrls, knowledge_ws: WorkspaceHandle, clock: FixedClock, tmp_path: Path
) -> None:
    """T-P3-12-11
    The fixture vault committed to a bare repository: `GitReader` (the real git runner)
    clones it over `file://` into its data folder and the sync makes the vault's notes;
    after a new commit (a new note and an edit) the next sync pulls it, adds the new
    note's Document and versions the edited one. The clone's `.git/` is never read as
    vault content.
    """
    from tumnis.core.net import NetPolicy  # noqa: PLC0415

    _git = importlib.import_module("tumnis.modules.knowledge.adapters.obsidian.git")
    GitReader = _git.GitReader  # noqa: N806
    SubprocessGitRunner = _git.SubprocessGitRunner  # noqa: N806
    vault_sync = importlib.import_module("tumnis.modules.knowledge.obsidian.sync")
    _rules = importlib.import_module("tumnis.modules.knowledge.obsidian.rules")
    VaultMapping = _rules.VaultMapping  # noqa: N806

    bare = tmp_path / "remote.git"
    bare.mkdir()
    git("init", "--bare", "--initial-branch=main", cwd=bare)
    work = copy_vault(tmp_path / "work")
    git("init", "--initial-branch=main", cwd=work)
    git("add", "-A", cwd=work)
    git("commit", "-m", "vault", cwd=work)
    git("push", str(bare), "HEAD:refs/heads/main", cwd=work)

    env = await vault_env(knowledge_ws, clock)
    reader = GitReader(
        connection_id=env.connection_id,
        remote=f"file://{bare}",
        branch="main",
        data_dir=tmp_path / "obsidian",
        deploy_key=None,
        known_hosts=None,
        runner=SubprocessGitRunner(),
        net=NetPolicy(mode="self-hosted"),
        clock=clock,
    )
    await reader.connect()
    mapping = VaultMapping(folders={"Clients/Acme": "acme"})

    async def sync() -> None:
        await vault_sync.sync_vault(
            knowledge_ws.ctx, env.connection_id, reader, mapping, extract=env.extract
        )

    await sync()
    first = await vault_documents(env)
    assert "Inbox/Rates.md" in first
    assert not any(path.startswith(".git") for path in first)
    assert first["Clients/Acme/Kickoff.md"]["project_id"] == env.projects["acme"]

    (work / "Inbox/New.md").write_text("---\ntumnis_project: lab\n---\n# New\n\nFresh note.\n")
    rates = work / "Inbox/Rates.md"
    rates.write_text(rates.read_text() + "\nNet 45 days for new clients.\n")
    git("add", "-A", cwd=work)
    git("commit", "-m", "more", cwd=work)
    git("push", str(bare), "HEAD:refs/heads/main", cwd=work)

    await sync()
    second = await vault_documents(env)
    assert second["Inbox/New.md"]["project_id"] == env.projects["lab"]
    assert second["Inbox/Rates.md"]["id"] == first["Inbox/Rates.md"]["id"]
    assert await version_numbers(env, second["Inbox/Rates.md"]["id"]) == [1, 2]
    assert "Net 45 days" in second["Inbox/Rates.md"]["body_md"]
