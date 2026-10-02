"""Setting up an Obsidian vault connection (P3-12, FR-15.10, Scott decision 82): hosted mode
offers Git only, a Git vault gets its own sealed deploy key, the worker previews the
mapping and then connects and syncs a folder vault, and a key that can write refuses the
connection."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.knowledge.tests.integration._vault import VaultEnv, copy_vault, vault_documents

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from dbos import DBOS

    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.knowledge.tests.integration.conftest import ExtractEnv

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

REMOTE = "ssh://git@vault.example.com/scott/notes.git"


def _net(mode: str) -> Any:
    from tumnis.core.net import NetPolicy  # noqa: PLC0415

    return NetPolicy(mode=mode)  # type: ignore[arg-type]


async def _connection(ws: WorkspaceHandle, connection_id: UUID) -> dict[str, Any]:
    from sqlalchemy import text  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    async with tenant_session(ws.ctx) as s:
        row = (
            await s.execute(
                text("SELECT kind, provider, status, last_error FROM connections WHERE id = :id"),
                {"id": connection_id},
            )
        ).one()
    return dict(row._mapping)  # SQLAlchemy's documented row mapping


def _verb(argv: list[str]) -> str:
    """The git subcommand: the first word after `git` and its `-c k=v` / `-C dir` pairs."""
    rest = argv[1:]
    while rest and rest[0] in {"-c", "-C"}:
        rest = rest[2:]
    return rest[0]


async def _until(check: Any, *, timeout_s: float = 90.0) -> Any:
    deadline = time.monotonic() + timeout_s
    while True:
        found = await check()
        if found is not None:
            return found
        assert time.monotonic() < deadline, "timed out waiting for the worker"
        await asyncio.sleep(0.25)


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
async def test_hosted_offers_git_only_with_a_sealed_deploy_key(
    knowledge_ws: WorkspaceHandle,
) -> None:
    """Hosted mode refuses a folder vault (422 `folder_not_offered`) and says so in the
    list; a Git vault answers its deploy key's public half and keeps the private half
    sealed in its connection, a module-owned `knowledge`/`obsidian` connection. Deleting
    the vault drops it from the list, overwrites the key and disables the connection."""
    import asyncssh  # noqa: PLC0415

    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.integrations import api as integrations  # noqa: PLC0415
    from tumnis.modules.knowledge.obsidian import vaults  # noqa: PLC0415

    ws = knowledge_ws
    hosted = _net("hosted")
    async with tenant_session(ws.ctx) as s:
        with pytest.raises(ProblemError) as refused:
            await vaults.create_vault(ws.ctx, s, vaults.VaultCreateIn(mode="folder"), net=hosted)
    assert (refused.value.status, refused.value.code) == (422, "folder_not_offered")
    async with tenant_session(ws.ctx) as s:
        made = await vaults.create_vault(ws.ctx, s, vaults.VaultCreateIn(mode="git"), net=hosted)
    assert made.status == "pending"
    assert made.deploy_public_key is not None
    assert made.deploy_public_key.startswith("ssh-ed25519 ")
    creds = await integrations.get_credentials(ws.ctx, made.id)
    assert creds is not None
    private = asyncssh.import_private_key(creds["deploy_key"])
    assert private.export_public_key("openssh").decode().strip() == made.deploy_public_key
    assert await _connection(ws, made.id) == {
        "kind": "knowledge",
        "provider": "obsidian",
        "status": "pending_auth",
        "last_error": None,
    }
    async with tenant_session(ws.ctx) as s:
        listed = await vaults.list_vaults(s, net=hosted)
    assert listed.folder_allowed is False
    assert [v.id for v in listed.vaults] == [made.id]

    async with tenant_session(ws.ctx) as s:
        await vaults.delete_vault(ws.ctx, s, made.id)
    async with tenant_session(ws.ctx) as s:
        assert (await vaults.list_vaults(s, net=_net("self-hosted"))).vaults == []
    assert await integrations.get_credentials(ws.ctx, made.id) == {}
    assert (await _connection(ws, made.id))["status"] == "disabled"


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
async def test_folder_vault_previews_then_connects_and_syncs(
    extract_env: ExtractEnv, dbos: type[DBOS], clock: FixedClock, tmp_path: Path
) -> None:
    """The worker previews the mapping (nothing written), then Connect saves the settings
    and the worker syncs the vault: the vault is `ok` with `last_sync_at`, its connection
    `ok`, and its notes are Documents."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge.obsidian import vaults  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    ws = extract_env.ws
    net = _net("self-hosted")
    folder = copy_vault(tmp_path / "vault")
    async with tenant_session(ws.ctx) as s:
        acme = await projects.create_project(
            s, ws.ctx.actor, projects.ProjectCreate(name="Acme"), now=clock.now()
        )
        made = await vaults.create_vault(ws.ctx, s, vaults.VaultCreateIn(mode="folder"), net=net)
    settings = vaults.VaultSettingsIn(
        mode="folder",
        folder_path=str(folder),
        folders=[vaults.FolderRule(folder="Clients/Acme", project_id=acme.id)],
    )

    started = await vaults.start_preview(ws.ctx, made.id, settings, net=net)

    async def previewed() -> vaults.PreviewOut | None:
        out = await vaults.preview_result(ws.ctx, made.id, started.preview_id)
        return None if out.status == "running" else out

    preview = await _until(previewed)
    assert preview.status == "done", preview
    rows = {row.path: row for row in preview.rows}
    assert rows["Clients/Acme/Kickoff.md"].project_id == acme.id
    assert rows["Clippings/Article.md"].untrusted
    assert not any(path.startswith(("Templates/", ".obsidian/")) for path in rows)
    env = VaultEnv(ws=ws, connection_id=made.id, projects={"acme": acme.id})
    assert await vault_documents(env) == {}

    connecting = await vaults.connect(ws.ctx, made.id, settings, net=net)
    assert connecting.status == "connecting"

    async def synced() -> vaults.VaultOut | None:
        async with tenant_session(ws.ctx) as s:
            out = await vaults.get_vault(s, made.id)
        return None if out.status == "connecting" else out

    done = await _until(synced)
    assert (done.status, done.last_error) == ("ok", None)
    assert done.last_sync_at is not None
    assert done.folder_path == str(folder)
    assert (await _connection(ws, made.id))["status"] == "ok"
    docs = await vault_documents(env)
    assert docs["Clients/Acme/Kickoff.md"]["project_id"] == acme.id
    assert "Templates/Meeting.md" not in docs


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
async def test_connect_refuses_a_deploy_key_that_can_write(
    knowledge_ws: WorkspaceHandle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write probe that succeeds refuses the connection: the vault is `error` with
    `writable_deploy_key`, its connection `auth_required`, the clone removed, and nothing
    synced. Git only ever cloned and dry-ran the push."""
    import asyncssh  # noqa: PLC0415
    from sqlalchemy import update  # noqa: PLC0415

    from tumnis.core.net import ScriptedResolver  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import pipeline  # noqa: PLC0415
    from tumnis.modules.knowledge.adapters.obsidian.fake import FakeGitRunner  # noqa: PLC0415
    from tumnis.modules.knowledge.models import ObsidianVault  # noqa: PLC0415
    from tumnis.modules.knowledge.obsidian import vaults  # noqa: PLC0415

    ws = knowledge_ws
    net = _net("self-hosted")
    previous = pipeline.configure(
        pipeline.current().model_copy(update={"obsidian_dir": str(tmp_path / "clones")})
    )
    runner = FakeGitRunner()
    runner.script("push", returncode=0, stderr="To vault.example.com\n * [new branch]\n")
    vaults.use_git_runner(lambda: runner, resolver=ScriptedResolver([["203.0.113.10"]] * 4))
    try:
        host = asyncssh.generate_private_key("ssh-ed25519").export_public_key("openssh").decode()
        known = "vault.example.com " + " ".join(host.split()[:2])
        async with tenant_session(ws.ctx) as s:
            made = await vaults.create_vault(ws.ctx, s, vaults.VaultCreateIn(mode="git"), net=net)
            row = await vaults._row(s, made.id)
            values = await vaults._checked(  # what connect saves
                s,
                row,
                vaults.VaultSettingsIn(mode="git", remote=REMOTE, known_hosts=known),
                net,
            )
            await s.execute(
                update(ObsidianVault.__table__)  # type: ignore[arg-type]
                .where(ObsidianVault.__table__.c.id == row["id"])
                .values(**values, status="connecting")
            )
        result = await vaults.run_connect(str(ws.ctx.workspace_id), str(made.id), net=net)
    finally:
        vaults.use_git_runner(None)
        pipeline.configure(previous)

    assert result == {"status": "error", "error": "writable_deploy_key"}
    async with tenant_session(ws.ctx) as s:
        out = await vaults.get_vault(s, made.id)
    assert (out.status, out.last_error) == ("error", "writable_deploy_key")
    assert (await _connection(ws, made.id))["status"] == "auth_required"
    assert not (tmp_path / "clones" / str(made.id)).exists()
    assert [_verb(call.argv) for call in runner.calls] == ["clone", "push"]
    assert "--dry-run" in runner.calls[1].argv
    env = VaultEnv(ws=ws, connection_id=made.id, projects={})
    assert await vault_documents(env) == {}


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
async def test_sync_tick_keeps_clones_in_use_and_disconnect_wins(
    knowledge_ws: WorkspaceHandle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 15-minute tick syncs only connected (`ok`) vaults. Its clone pruning removes the
    clones of deleted and refused (`error`) Git vaults but keeps those of vaults still being
    set up (`pending`, a preview) or connected (`connecting`), whose own workflow may be
    reading them. A status a sync writes after the vault was disconnected changes nothing:
    the connection stays `disabled`."""
    from sqlalchemy import update  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge.models import ObsidianVault  # noqa: PLC0415
    from tumnis.modules.knowledge.obsidian import vaults  # noqa: PLC0415

    ws = knowledge_ws
    root = tmp_path / "clones"
    monkeypatch.setattr(vaults, "data_dir", lambda: root)

    async def git_vault(status: str) -> UUID:
        async with tenant_session(ws.ctx) as s:
            made = await vaults.create_vault(
                ws.ctx, s, vaults.VaultCreateIn(mode="git"), net=_net("self-hosted")
            )
        async with tenant_session(ws.ctx) as s:
            await s.execute(
                update(ObsidianVault)
                .where(ObsidianVault.connection_id == made.id)
                .values(status=status)
            )
        (root / str(made.id)).mkdir(parents=True)
        return made.id

    made = {status: await git_vault(status) for status in ("pending", "connecting", "ok", "error")}
    gone = await git_vault("ok")
    async with tenant_session(ws.ctx) as s:
        await vaults.delete_vault(ws.ctx, s, gone)

    synced = await vaults.vaults_to_sync()
    mine = {str(cid) for cid in (*made.values(), gone)}
    assert [cid for _ws, cid in synced if cid in mine] == [str(made["ok"])]
    assert {child.name for child in root.iterdir()} == {
        str(made[status]) for status in ("pending", "connecting", "ok")
    }

    async with tenant_session(ws.ctx) as s:
        await vaults.delete_vault(ws.ctx, s, made["ok"])
    await vaults._set_status(ws.ctx, made["ok"], "ok", last_error=None)
    assert (await _connection(ws, made["ok"]))["status"] == "disabled"
