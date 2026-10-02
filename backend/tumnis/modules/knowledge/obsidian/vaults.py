"""Obsidian vault connections (P3-12, FR-15.10): setting one up from Settings, and the
worker's side of previewing, connecting and syncing it. The router calls the api half;
`knowledge.workflows` calls the worker half.

Connection rows: a vault's connection is knowledge's own, made with
`integrations.upsert_connection` (kind `knowledge`, provider `obsidian`, account
`obsidian:<uuid>`), as P3-13's linked buckets are. `obsidian` is not a P3-02 framework
provider: a vault has no sign-in and no canonical records, so it syncs on knowledge's own
`knowledge-obsidian-sync-tick` and P3-02's tick never sees it. Its settings and the status
Settings shows live in `obsidian_vaults`; the connection's status follows them
(`set_connection_status`).

Setting one up (ObsidianSetup):

1. `create_vault(mode)`: the connection, `pending_auth`, and its `obsidian_vaults` row.
   Git: a fresh ed25519 deploy key (asyncssh `generate_private_key("ssh-ed25519")`), its
   private half sealed with the workspace data key in the connection's credentials
   (`put_credentials`), its public half answered for the person to add to the Git host
   as a read-only deploy key. Hosted mode offers Git only (422 `folder_not_offered`).
2. `probe_host_key(remote)`: the SSH host key the remote shows now, with its SHA256
   fingerprint, for the person to confirm (nothing is stored; decision 82).
3. `start_preview(settings)`: the mapping as it would sync, computed by the worker
   (`knowledge_obsidian_preview`; a Git remote is cloned there), polled with
   `preview_result`. Nothing is written to the knowledge base.
4. `connect(settings)`: the settings saved with the pinned known_hosts line (the confirmed
   probe answer, or a line the person pasted; `setup.pinned_known_hosts` checks it), then
   `knowledge_obsidian_connect`: Git clones afresh and runs the write probe (a key that can
   write, or a probe that proves nothing, is refused: status `error`, connection
   `auth_required`), then the first sync.
5. `delete_vault`: the vault row soft-deleted, the sealed key overwritten, the connection
   `disabled`; its Documents stay, and are no longer read-only (`api._refuse_synced`).

A sync (`run_sync`) refused by the Git host (`host_key_changed`, `writable_deploy_key`)
puts the vault in `error` until the person connects it again; an unreachable host keeps
it `ok` with `last_error` set, the connection `degraded`, and the next tick tries again.
"""

import asyncio
import shutil
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal
from uuid import UUID

import asyncssh
from pydantic import BaseModel, Field
from sqlalchemy import RowMapping, Table, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import deadletter
from tumnis.core.adapters.errors import AdapterError, AdapterRejected
from tumnis.core.clock import SystemClock
from tumnis.core.errors import ProblemError
from tumnis.core.ids import uuid7
from tumnis.core.net import NetPolicy, Resolver, SsrfBlocked, system_resolver
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.core.versioning import NotFound
from tumnis.modules.integrations import api as integrations
from tumnis.modules.knowledge import pipeline
from tumnis.modules.knowledge.adapters.obsidian.folder import FolderReader
from tumnis.modules.knowledge.adapters.obsidian.git import (
    GitReader,
    GitRunner,
    InvalidRemote,
    parse_remote,
)
from tumnis.modules.knowledge.adapters.obsidian.port import VaultReader
from tumnis.modules.knowledge.adapters.sftp import fingerprint
from tumnis.modules.knowledge.models import ObsidianVault
from tumnis.modules.knowledge.obsidian import setup
from tumnis.modules.knowledge.obsidian import sync as vault_sync
from tumnis.modules.knowledge.obsidian.rules import VaultMapping, project_slug
from tumnis.modules.knowledge.storage import StorageError
from tumnis.modules.projects import api as projects

__all__ = [
    "CONNECT_WORKFLOW",
    "PREVIEW_WORKFLOW",
    "PROVIDER",
    "SYNC_QUEUE",
    "SYNC_WORKFLOW",
    "HostKeyOut",
    "HostKeyProbeIn",
    "PreviewOut",
    "PreviewStarted",
    "VaultCreateIn",
    "VaultOut",
    "VaultSettingsIn",
    "VaultsOut",
    "connect",
    "create_vault",
    "delete_vault",
    "get_vault",
    "list_vaults",
    "new_deploy_key",
    "preview_result",
    "probe_host_key",
    "start_preview",
    "use_git_runner",
]

PROVIDER: Final = "obsidian"  # connections.provider of a vault
SYNC_QUEUE: Final = "sync"
CONNECT_WORKFLOW: Final = "knowledge_obsidian_connect"
SYNC_WORKFLOW: Final = "knowledge_obsidian_sync"
PREVIEW_WORKFLOW: Final = "knowledge_obsidian_preview"
PREVIEW_PREFIX: Final = "obsidian-preview"
KEY_COMMENT: Final = "tumnis-obsidian"
MAX_RULES: Final = 200

_vaults: Table = ObsidianVault.__table__  # type: ignore[assignment]

Mode = Literal["folder", "git"]
VaultStatus = Literal["pending", "connecting", "ok", "error"]


# --- Shapes ------------------------------------------------------------------------------


class FolderRule(BaseModel):
    """A vault folder and the project its notes go to (the longest matching folder wins)."""

    folder: str = Field(min_length=1, max_length=500)
    project_id: UUID


class VaultCreateIn(BaseModel):
    mode: Mode


class VaultSettingsIn(BaseModel):
    """Where the vault is read from and how its notes map (ObsidianSetup's form). For Git,
    `known_hosts` is the host key line the person confirmed or pasted."""

    mode: Mode
    folder_path: str | None = Field(default=None, max_length=1024)
    remote: str | None = Field(default=None, max_length=500)
    branch: str = Field(default="main", min_length=1, max_length=200)
    folders: list[FolderRule] = Field(default_factory=list, max_length=MAX_RULES)
    unmapped: Literal["workspace", "ignore"] = "workspace"
    clippings_folder: str = Field(default="Clippings", min_length=1, max_length=500)
    extra_excludes: list[str] = Field(default_factory=list, max_length=50)
    known_hosts: str | None = Field(default=None, max_length=4000)


class VaultOut(BaseModel):
    id: UUID  # the connection's id
    mode: Mode
    folder_path: str | None
    remote: str | None
    branch: str
    folders: list[FolderRule]
    unmapped: Literal["workspace", "ignore"]
    clippings_folder: str
    extra_excludes: list[str]
    host_key_sha256: str | None  # the pinned host key's fingerprint (Git)
    deploy_public_key: str | None  # add this to the Git host as a read-only deploy key
    status: VaultStatus
    last_error: str | None
    last_sync_at: datetime | None
    version: int


class VaultsOut(BaseModel):
    folder_allowed: bool  # False in hosted mode: a vault comes from Git only
    vaults: list[VaultOut]


class HostKeyProbeIn(BaseModel):
    remote: str = Field(min_length=1, max_length=500)


class HostKeyOut(BaseModel):
    sha256: str
    known_hosts: str


class PreviewStarted(BaseModel):
    preview_id: str


class PreviewRowOut(BaseModel):
    path: str
    project_id: UUID | None
    ignored: bool
    untrusted: bool


class PreviewOut(BaseModel):
    status: Literal["running", "done", "failed"]
    rows: list[PreviewRowOut]
    error: str | None  # the refusal's code when the vault could not be read


# --- Test seam ---------------------------------------------------------------------------


class _Seams:
    runner: Callable[[], GitRunner | None] = staticmethod(lambda: None)  # None: real git
    resolver: Resolver = system_resolver


def use_git_runner(
    factory: Callable[[], GitRunner | None] | None, *, resolver: Resolver | None = None
) -> None:
    """Run Git through `factory()` (a test's FakeGitRunner) and resolve Git hosts with
    `resolver`; None puts real git and DNS back."""
    _Seams.runner = staticmethod(factory or (lambda: None))
    _Seams.resolver = resolver or system_resolver


def new_deploy_key() -> tuple[str, str]:
    """A fresh ed25519 key pair for a vault's Git remote: (private half in OpenSSH form, to
    seal; public half as one authorized_keys line, to show)."""
    key = asyncssh.generate_private_key("ssh-ed25519", comment=KEY_COMMENT)
    return (
        key.export_private_key("openssh").decode(),
        key.export_public_key("openssh").decode().strip(),
    )


# --- The api half ------------------------------------------------------------------------


def _no_preview() -> ProblemError:
    return ProblemError(404, "not_found", "There is no such preview.")


def _bad(code: str, detail: str) -> ProblemError:
    return ProblemError(422, code, detail)


def _mapping_json(body: VaultSettingsIn) -> dict[str, Any]:
    return {
        "folders": [rule.model_dump(mode="json") for rule in body.folders],
        "unmapped": body.unmapped,
        "clippings_folder": body.clippings_folder.strip("/"),
        "extra_excludes": [item.strip("/") for item in body.extra_excludes if item.strip("/")],
    }


def _out(row: Mapping[Any, Any]) -> VaultOut:
    mapping = row["mapping"] or {}
    known = row["known_hosts"]
    return VaultOut(
        id=row["connection_id"],
        mode=row["mode"],
        folder_path=row["folder_path"],
        remote=row["remote"],
        branch=row["branch"],
        folders=[FolderRule.model_validate(rule) for rule in mapping.get("folders", [])],
        unmapped=mapping.get("unmapped", "workspace"),
        clippings_folder=mapping.get("clippings_folder", "Clippings"),
        extra_excludes=list(mapping.get("extra_excludes", [])),
        host_key_sha256=fingerprint(" ".join(known.split()[1:3])) if known else None,
        deploy_public_key=row["deploy_public_key"],
        status=row["status"],
        last_error=row["last_error"],
        last_sync_at=row["last_sync_at"],
        version=row["version"],
    )


async def _row(s: AsyncSession, connection_id: UUID) -> RowMapping:
    row = (
        (
            await s.execute(
                select(_vaults).where(
                    _vaults.c.connection_id == connection_id, _vaults.c.deleted_at.is_(None)
                )
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("obsidian_vaults", connection_id)
    return row


def _refuse_folder(net: NetPolicy) -> None:
    if net.mode == "hosted":
        raise _bad("folder_not_offered", "Hosted Tumnis reads an Obsidian vault from Git only.")


async def create_vault(
    ctx: WorkspaceContext, s: AsyncSession, body: VaultCreateIn, *, net: NetPolicy
) -> VaultOut:
    """A new vault connection, `pending` until it is connected; for Git, with its deploy
    key made (the public half answered, the private half sealed)."""
    if body.mode == "folder":
        _refuse_folder(net)
    connection_id = await integrations.upsert_connection(
        ctx,
        kind="knowledge",
        provider=PROVIDER,
        account=f"obsidian:{uuid7()}",
        status="pending_auth",
        session=s,
    )
    public_key = None
    if body.mode == "git":
        private, public_key = new_deploy_key()
        await integrations.put_credentials(ctx, connection_id, {"deploy_key": private}, session=s)
    row = (
        (
            await s.execute(
                _vaults.insert()
                .values(connection_id=connection_id, mode=body.mode, deploy_public_key=public_key)
                .returning(*_vaults.c)
            )
        )
        .mappings()
        .one()
    )
    return _out(row)


async def list_vaults(s: AsyncSession, *, net: NetPolicy) -> VaultsOut:
    rows = (
        (
            await s.execute(
                select(_vaults).where(_vaults.c.deleted_at.is_(None)).order_by(_vaults.c.id)
            )
        )
        .mappings()
        .all()
    )
    return VaultsOut(folder_allowed=net.mode != "hosted", vaults=[_out(row) for row in rows])


async def get_vault(s: AsyncSession, connection_id: UUID) -> VaultOut:
    return _out(await _row(s, connection_id))


async def probe_host_key(
    body: HostKeyProbeIn, *, net: NetPolicy, resolver: Resolver = system_resolver
) -> HostKeyOut:
    """The host key the remote's SSH server shows now (decision 82: shown to confirm,
    never stored here)."""
    try:
        found = await setup.probe_git_host(body.remote, net=net, resolver=resolver)
    except InvalidRemote as exc:
        raise _bad("invalid_remote", exc.message) from exc
    except SsrfBlocked as exc:
        raise _bad("ssrf_blocked", "That Git host is not allowed.") from exc
    except AdapterError as exc:
        raise ProblemError(503, "host_unreachable", "The Git host did not answer.") from exc
    return HostKeyOut(sha256=found.sha256, known_hosts=found.known_hosts)


async def _checked(
    s: AsyncSession, row: Mapping[Any, Any], body: VaultSettingsIn, net: NetPolicy
) -> dict[str, Any]:
    """The row values the settings make, after every rule: the mode is the vault's; a
    folder is an absolute path (never in hosted mode); a remote parses under the net policy
    and an SSH remote has a host key line for its host; every mapped project exists."""
    if body.mode != row["mode"]:
        raise _bad("invalid_vault", "A vault keeps the mode it was made with.")
    values: dict[str, Any] = {"mapping": _mapping_json(body), "branch": body.branch}
    if body.mode == "folder":
        _refuse_folder(net)
        path = PurePosixPath(body.folder_path or "")
        if not path.is_absolute() or ".." in path.parts:
            raise _bad("invalid_vault", "The vault folder must be an absolute path.")
        values |= {"folder_path": str(path), "remote": None, "known_hosts": None}
    else:
        try:
            remote = parse_remote(body.remote or "", net)
        except InvalidRemote as exc:
            raise _bad("invalid_remote", exc.message) from exc
        known = None
        if remote.kind == "ssh":
            if not body.known_hosts:
                raise _bad("host_key_required", "Confirm the Git host's key first.")
            try:
                known = setup.pinned_known_hosts(
                    body.remote or "", body.known_hosts, net=net
                ).known_hosts
            except (setup.InvalidHostKey, InvalidRemote) as exc:
                raise _bad(getattr(exc, "code", "invalid_host_key"), exc.message) from exc
        values |= {"folder_path": None, "remote": body.remote, "known_hosts": known}
    for rule in body.folders:
        if not await projects.project_exists(s, rule.project_id):
            raise NotFound("projects", rule.project_id)
    return values


async def start_preview(
    ctx: WorkspaceContext, connection_id: UUID, body: VaultSettingsIn, *, net: NetPolicy
) -> PreviewStarted:
    """Queue the mapping preview for these settings on the worker; nothing is saved."""
    async with tenant_session(ctx) as s:
        values = await _checked(s, await _row(s, connection_id), body, net)
    preview_id = f"{PREVIEW_PREFIX}:{connection_id}:{uuid7()}"
    await deadletter.dbos_client().enqueue_async(
        {"queue_name": SYNC_QUEUE, "workflow_name": PREVIEW_WORKFLOW, "workflow_id": preview_id},
        str(ctx.workspace_id),
        str(connection_id),
        {key: values[key] for key in ("folder_path", "remote", "branch", "known_hosts", "mapping")},
    )
    return PreviewStarted(preview_id=preview_id)


async def preview_result(ctx: WorkspaceContext, connection_id: UUID, preview_id: str) -> PreviewOut:
    """How the preview stands: running, done with its rows, or failed with a code."""
    async with tenant_session(ctx) as s:
        await _row(s, connection_id)
    if not preview_id.startswith(f"{PREVIEW_PREFIX}:{connection_id}:"):
        raise _no_preview()
    from dbos import error as dbos_error  # noqa: PLC0415  # only this route asks DBOS

    try:
        handle: Any = await deadletter.dbos_client().retrieve_workflow_async(preview_id)
    except dbos_error.DBOSNonExistentWorkflowError:
        raise _no_preview() from None
    status = await handle.get_status()
    if status.status in ("ENQUEUED", "PENDING", "DELAYED"):
        return PreviewOut(status="running", rows=[], error=None)
    output = status.output if status.status == "SUCCESS" else None
    if not isinstance(output, dict) or output.get("error"):
        code = output.get("error") if isinstance(output, dict) else "preview_failed"
        return PreviewOut(status="failed", rows=[], error=str(code))
    return PreviewOut(
        status="done",
        rows=[PreviewRowOut.model_validate(item) for item in output.get("rows", [])],
        error=None,
    )


async def connect(
    ctx: WorkspaceContext, connection_id: UUID, body: VaultSettingsIn, *, net: NetPolicy
) -> VaultOut:
    """Save the settings (and the pinned host key), then queue the connect: Git's fresh
    clone and write probe, then the first sync."""
    async with tenant_session(ctx) as s:
        row = await _row(s, connection_id)
        values = await _checked(s, row, body, net)
        saved = (
            (
                await s.execute(
                    update(_vaults)
                    .where(_vaults.c.id == row["id"])
                    .values(**values, status="connecting", last_error=None)
                    .returning(*_vaults.c)
                )
            )
            .mappings()
            .one()
        )
    await deadletter.dbos_client().enqueue_async(
        {
            "queue_name": SYNC_QUEUE,
            "workflow_name": CONNECT_WORKFLOW,
            "workflow_id": f"obsidian-connect:{connection_id}:{uuid7()}",
        },
        str(ctx.workspace_id),
        str(connection_id),
    )
    return _out(saved)


async def delete_vault(ctx: WorkspaceContext, s: AsyncSession, connection_id: UUID) -> None:
    """Stop syncing the vault: the row soft-deleted, the sealed deploy key overwritten
    (a module-owned connection has no framework `disconnect`), the connection disabled.
    Its Documents stay, editable from now on; the worker's clone goes at the next tick."""
    row = await _row(s, connection_id)
    await s.execute(
        update(_vaults).where(_vaults.c.id == row["id"]).values(deleted_at=SystemClock().now())
    )
    if row["mode"] == "git":
        await integrations.put_credentials(ctx, connection_id, {}, session=s)
    await integrations.set_connection_status(ctx, connection_id, "disabled", session=s)


# --- The worker half ---------------------------------------------------------------------


def data_dir() -> Path:
    return Path(pipeline.current().obsidian_dir)


async def _deploy_key(ctx: WorkspaceContext, connection_id: UUID) -> bytes | None:
    creds = await integrations.get_credentials(ctx, connection_id)
    key = (creds or {}).get("deploy_key")
    return key.encode() if isinstance(key, str) else None


async def _reader(
    ctx: WorkspaceContext, connection_id: UUID, values: Mapping[Any, Any], net: NetPolicy
) -> VaultReader:
    if values.get("folder_path"):
        return FolderReader(values["folder_path"])
    return GitReader(
        connection_id=connection_id,
        remote=values["remote"],
        branch=values["branch"],
        data_dir=data_dir(),
        deploy_key=await _deploy_key(ctx, connection_id),
        known_hosts=values.get("known_hosts"),
        runner=_Seams.runner(),
        net=net,
        resolver=_Seams.resolver,
    )


async def _mapping(ctx: WorkspaceContext, raw: Mapping[Any, Any]) -> VaultMapping:
    """The stored mapping as the sync reads it: each rule's project by `project_slug` of
    its name (a project gone or renamed since maps nothing)."""
    rules = [FolderRule.model_validate(rule) for rule in raw.get("folders", [])]
    async with tenant_session(ctx) as s:
        names = await projects.project_names(s, [rule.project_id for rule in rules])
    return VaultMapping(
        folders={
            rule.folder.strip("/"): project_slug(names[rule.project_id])
            for rule in rules
            if rule.project_id in names
        },
        unmapped=raw.get("unmapped", "workspace"),
        clippings_folder=raw.get("clippings_folder", "Clippings"),
        extra_excludes=tuple(raw.get("extra_excludes", ())),
    )


def _code(exc: Exception) -> str:
    return str(
        getattr(exc, "code", None)
        or ("rejected" if isinstance(exc, AdapterRejected) else "unavailable")
    )


async def run_preview(
    workspace_id: str, connection_id: str, values: Mapping[Any, Any], *, net: NetPolicy
) -> dict[str, Any]:
    """The worker's preview: JSON rows, or {"error": code} when the vault cannot be read."""
    ctx = WorkspaceContext(UUID(workspace_id), SYSTEM_ACTOR)
    try:
        reader = await _reader(ctx, UUID(connection_id), values, net)
        rows = await vault_sync.preview_mapping(ctx, reader, await _mapping(ctx, values["mapping"]))
    except (AdapterError, StorageError, OSError) as exc:
        return {"error": _code(exc), "rows": []}
    return {
        "rows": [
            {
                "path": row.path,
                "project_id": str(row.project_id) if row.project_id else None,
                "ignored": row.ignored,
                "untrusted": row.untrusted,
            }
            for row in rows
        ]
    }


async def _vault_values(ctx: WorkspaceContext, connection_id: UUID) -> dict[str, Any] | None:
    async with tenant_session(ctx) as s:
        try:
            return dict(await _row(s, connection_id))
        except NotFound:
            return None


async def _set_status(
    ctx: WorkspaceContext,
    connection_id: UUID,
    status: VaultStatus,
    *,
    last_error: str | None,
    synced_at: datetime | None = None,
) -> None:
    values: dict[str, Any] = {"status": status, "last_error": last_error}
    if synced_at is not None:
        values["last_sync_at"] = synced_at
    if status == "error":
        connection = "auth_required"
    elif last_error:
        connection = "degraded"
    else:
        connection = "ok"
    async with tenant_session(ctx) as s:
        await s.execute(
            update(_vaults)
            .where(_vaults.c.connection_id == connection_id, _vaults.c.deleted_at.is_(None))
            .values(**values)
        )
        await integrations.set_connection_status(
            ctx, connection_id, connection, last_error=last_error, last_sync_at=synced_at, session=s
        )


async def run_connect(workspace_id: str, connection_id: str, *, net: NetPolicy) -> dict[str, Any]:
    """Git's fresh clone and write probe (refused: `error`), then the first sync."""
    ctx = WorkspaceContext(UUID(workspace_id), SYSTEM_ACTOR)
    cid = UUID(connection_id)
    values = await _vault_values(ctx, cid)
    if values is None:
        return {"status": "gone"}
    if values["mode"] == "git":
        try:
            reader = await _reader(ctx, cid, values, net)
            assert isinstance(reader, GitReader)  # noqa: S101  # mode git makes a GitReader
            await reader.connect()
        except (AdapterError, OSError) as exc:
            await _set_status(ctx, cid, "error", last_error=_code(exc))
            return {"status": "error", "error": _code(exc)}
    return await run_sync(workspace_id, connection_id, net=net, connecting=True)


async def run_sync(
    workspace_id: str, connection_id: str, *, net: NetPolicy, connecting: bool = False
) -> dict[str, Any]:
    """One scan of a connected vault (`sync_vault`), its outcome on the vault's status."""
    ctx = WorkspaceContext(UUID(workspace_id), SYSTEM_ACTOR)
    cid = UUID(connection_id)
    values = await _vault_values(ctx, cid)
    if values is None:
        return {"status": "gone"}
    if values["status"] not in (("connecting", "ok") if connecting else ("ok",)):
        return {"status": values["status"]}
    try:
        reader = await _reader(ctx, cid, values, net)
        report = await vault_sync.sync_vault(
            ctx, cid, reader, await _mapping(ctx, values["mapping"])
        )
    except AdapterRejected as exc:
        await _set_status(ctx, cid, "error", last_error=_code(exc))
        return {"status": "error", "error": _code(exc)}
    except (AdapterError, StorageError, OSError) as exc:
        await _set_status(ctx, cid, "error" if connecting else "ok", last_error=_code(exc))
        return {"status": "error", "error": _code(exc)}
    await _set_status(ctx, cid, "ok", last_error=None, synced_at=SystemClock().now())
    return {
        "status": "ok",
        "created": report.created,
        "changed": report.changed,
        "trashed": report.trashed,
        "attachments": report.attachments,
    }


async def vaults_to_sync() -> list[tuple[str, str]]:
    """(workspace, connection) for every connected vault of every workspace; the clones
    of vaults no longer connected by Git are removed on the way."""
    found = await _all_vaults()
    await asyncio.to_thread(
        _prune_clones, {cid for _ws, cid, mode, _path in found if mode == "git"}
    )
    return [(ws, cid) for ws, cid, _mode, _path in found]


def _prune_clones(keep: set[str]) -> None:
    """Remove every clone under the data folder that is not a connected Git vault's (a
    deleted vault's, or one whose connect was refused)."""
    root = data_dir()
    if not root.is_dir():
        return
    for child in root.iterdir():
        if child.is_dir() and child.name not in keep:
            shutil.rmtree(child, ignore_errors=True)


async def watched_vaults() -> list[tuple[str, str, str]]:
    """(workspace, connection, folder) for every connected folder vault."""
    return [
        (ws, cid, path) for ws, cid, mode, path in await _all_vaults() if mode == "folder" and path
    ]


async def _all_vaults() -> list[tuple[str, str, str, str | None]]:
    from tumnis.core import audit, db  # noqa: PLC0415

    async with db.app_sessionmaker()() as s, s.begin():
        workspaces = await audit.workspace_ids(s)
    found: list[tuple[str, str, str, str | None]] = []
    for workspace_id in workspaces:
        async with tenant_session(WorkspaceContext(workspace_id, SYSTEM_ACTOR)) as s:
            rows = await s.execute(
                select(_vaults.c.connection_id, _vaults.c.mode, _vaults.c.folder_path).where(
                    _vaults.c.deleted_at.is_(None), _vaults.c.status == "ok"
                )
            )
            found += [
                (str(workspace_id), str(r.connection_id), r.mode, r.folder_path) for r in rows
            ]
    return found


def watched_vault(path: str, roots: list[tuple[str, str, str]]) -> tuple[str, str] | None:
    """The (workspace, connection) whose vault folder holds the changed `path`."""
    for workspace_id, connection_id, root in roots:
        if path.startswith(root.rstrip("/") + "/"):
            return workspace_id, connection_id
    return None
