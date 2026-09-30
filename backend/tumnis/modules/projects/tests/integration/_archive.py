"""The world the archive tests act on (P2-18). No assertions about the product live here.

`ArchiveWorld` holds one workspace with a server-path location on the test's disk (the
default location, so a project's Tumnis-made folder lands there), and drives the product
through its routes and apis:

- `project(name)`: `POST /v1/projects`, relayed (the project's folder is made).
- `folder(project_id)`: the project's folder on disk; `write(root, files)` writes files
  there as someone outside Tumnis would; `sync()` runs one `knowledge_folder_sync` of the
  location, as the worker does (so the files get their `folder_files` records; the
  folder sync extracts nothing here), then `settle()`s: the outbox relayed and every
  delivery run, so what earlier events do (a seed task's label) is done before a snapshot.
- `add_runs(project_id, ...)`: a daemon profile of the project (on `runner_id` when given)
  with finished runs and their `run_events` (owner SQL: runs are the agents module's).
- `add_context_items(project_id, n)`: context items owned by the project.
- `archive(project_id)` / `unarchive(project_id)`: the routes, then the relay, waiting
  until the project's `archive_state` settles (`archived`, then null).
- readers over the owner connection: `run_events`, `context_items`, `folder_files`,
  `chunks`, `blobs`, `archive_state`, `tree` (relative path -> sha256 of every file under
  a folder on disk).

Every location opens as a real `ServerPathStorage` (the app runs on fakes, where a
location would be an in-memory tree): `ArchiveWorld.close` puts the hooks back.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg

from tests._pg import OWNER
from tumnis.core.net import NetPolicy

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

SELF_HOSTED = NetPolicy(mode="self-hosted")
MARKER = ".tumnis-root"
SETTLE_S = 30.0


def owner_rows(db: DbUrls, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(query.encode(), params).fetchall()


def owner_exec(db: DbUrls, query: str, params: tuple[Any, ...] = ()) -> None:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        conn.execute(query.encode(), params)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _no_extraction(_workspace_id: uuid.UUID, _version_id: uuid.UUID, _path: str) -> None:
    """The folder sync's extraction, off: what an archive moves is the index as the test
    builds it, not what P1-16's pipeline would add to it meanwhile."""


def use_real_folders() -> Callable[[], None]:
    """Every location opens as a `ServerPathStorage` on its root, the folder sync and the
    archive steps use the self-hosted net policy, and the folder sync extracts nothing;
    returns the undo."""
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge import sync  # noqa: PLC0415
    from tumnis.modules.knowledge.adapters.server_path import ServerPathStorage  # noqa: PLC0415

    knowledge.use_backend_hook(lambda row, _built: ServerPathStorage(row["root"]))
    sync.configure(net=SELF_HOSTED)
    clock, extraction = sync.use(clock=None, extraction=_no_extraction)

    def undo() -> None:
        knowledge.use_backend_hook(None)
        sync.configure(net=None)
        sync.use(clock=clock, extraction=extraction)

    return undo


def load_archive_modules() -> None:
    """What the worker loads: every module's api, events and workflows (the archive hooks,
    the subscribers that start the workflows, and the workflows themselves)."""
    from tumnis import wiring  # noqa: PLC0415

    wiring.load_apis()
    wiring.load_events()
    wiring.load_workflows()


@dataclass
class ArchiveWorld:
    db: DbUrls
    ws: WorkspaceHandle
    clock: FixedClock
    client: SessionClient
    root: Path  # the location's root on disk
    location_id: uuid.UUID | None = None
    _undo: list[Callable[[], None]] = field(default_factory=list)

    async def start(self) -> None:
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

        load_archive_modules()
        self._undo.append(use_real_folders())
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / MARKER).write_text("tumnis\n")
        async with tenant_session(self.ws.ctx) as s:
            location = await knowledge.create_location(
                s,
                knowledge.LocationIn(
                    name="disk", kind="server_path", root=str(self.root), is_default=True
                ),
                net=SELF_HOSTED,
            )
        self.location_id = location.id

    def close(self) -> None:
        for undo in reversed(self._undo):
            undo()
        self._undo.clear()

    # --- the product ----------------------------------------------------------------------

    async def relay(self) -> None:
        from tumnis.core.events import relay_once  # noqa: PLC0415

        while await relay_once():
            pass

    async def settle(self) -> None:
        """Relay until the outbox is empty and every delivery has run, twice in a row: what
        the events so far do (a seed task's label, say) is done before the test looks."""
        from dbos import DBOS  # noqa: PLC0415

        from tumnis.core.events import relay_once  # noqa: PLC0415

        loop = asyncio.get_running_loop()
        deadline = loop.time() + SETTLE_S
        calm = 0
        while calm < 2:
            sent = await relay_once()
            busy = await DBOS.list_workflows_async(
                status=["ENQUEUED", "PENDING"], name="deliver_event", load_input=False
            )
            calm = calm + 1 if not sent and not busy else 0
            if loop.time() > deadline:
                raise AssertionError(f"deliveries still running after {SETTLE_S} s")
            await asyncio.sleep(0.1)

    async def project(self, name: str) -> uuid.UUID:
        created = await self.client.post("/v1/projects", json={"name": name})
        created.raise_for_status()
        project_id = uuid.UUID(created.json()["id"])
        await self.relay()
        return project_id

    async def folder(self, project_id: uuid.UUID) -> Path:
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

        async with tenant_session(self.ws.ctx) as s:
            made = await knowledge.ensure_project_folder(s, project_id, net=SELF_HOSTED)
        assert made is not None  # the location is the default
        return self.root / made.root_path

    @staticmethod
    def write(folder: Path, files: dict[str, bytes]) -> None:
        for rel, data in files.items():
            path = folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    async def sync(self) -> None:
        from dbos import DBOS  # noqa: PLC0415

        from tumnis.modules.knowledge.workflows import folder_sync  # noqa: PLC0415

        handle = await DBOS.start_workflow_async(
            folder_sync, str(self.ws.id), str(self.location_id)
        )
        await handle.get_result()
        await self.settle()

    def add_runs(
        self,
        project_id: uuid.UUID,
        *,
        runs: int = 3,
        events: int = 40,
        profile: str | None = None,
        runner_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """A daemon profile for the project (reused when it has one) with `runs` finished
        runs of `events` stream lines each; the profile's id."""
        found = owner_rows(
            self.db,
            "SELECT id FROM agent_profiles WHERE project_id = %s AND deleted_at IS NULL",
            (project_id,),
        )
        if found:
            profile_id = found[0][0]
            if runner_id is not None:
                owner_exec(
                    self.db,
                    "UPDATE agent_profiles SET runner_id = %s, status = 'ready' WHERE id = %s",
                    (runner_id, profile_id),
                )
        else:
            [(profile_id,)] = owner_rows(
                self.db,
                "INSERT INTO agent_profiles (workspace_id, name, role, project_id, runner_id,"
                " transport, status, created_by) VALUES (%s, %s, 'project', %s, %s, 'daemon',"
                " %s, 'system') RETURNING id",
                (
                    self.ws.id,
                    profile or f"p-{project_id.hex[:12]}",
                    project_id,
                    runner_id,
                    "ready" if runner_id is not None else "registered",
                ),
            )
        for _ in range(runs):
            [(run_id,)] = owner_rows(
                self.db,
                "INSERT INTO runs (workspace_id, profile_id, kind, status, correlation_id,"
                " created_by, started_at, finished_at) VALUES (%s, %s, 'task', 'succeeded',"
                " %s, 'system', now(), now()) RETURNING id",
                (self.ws.id, profile_id, f"run:{uuid.uuid4()}"),
            )
            for seq in range(1, events + 1):
                payload = {
                    "seq": seq,
                    "kind": "log",
                    "text": f"step {seq}: ran the test suite, all green, wrote the summary",
                }
                owner_exec(
                    self.db,
                    "INSERT INTO run_events (workspace_id, run_id, message_id, kind, payload,"
                    " created_by) VALUES (%s, %s, %s, 'log', %s::jsonb, 'system')",
                    (self.ws.id, run_id, uuid.uuid4(), json.dumps(payload)),
                )
        return uuid.UUID(str(profile_id))

    def add_context_items(self, project_id: uuid.UUID, n: int = 2) -> None:
        for i in range(n):
            owner_exec(
                self.db,
                "INSERT INTO context_items (workspace_id, owner_type, owner_id, target_type,"
                " target_url, tainted, added_by, created_by) VALUES (%s, 'project', %s, 'url',"
                " %s, true, 'user', 'system')",
                (self.ws.id, project_id, f"https://example.org/brief-{i}"),
            )

    async def _settle(self, project_id: uuid.UUID, want: str | None) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SETTLE_S
        while True:
            await self.relay()
            # the owner connection, not the route: polling the route hits its rate limit
            if self.archive_state(project_id) == want:
                return
            if loop.time() > deadline:
                raise AssertionError(f"archive_state never became {want!r}")
            await asyncio.sleep(0.1)

    async def _version(self, project_id: uuid.UUID) -> int:
        found = await self.client.get(f"/v1/projects/{project_id}")
        found.raise_for_status()
        version: int = found.json()["version"]
        return version

    async def archive(self, project_id: uuid.UUID) -> None:
        done = await self.client.post(
            f"/v1/projects/{project_id}/archive",
            json={"version": await self._version(project_id)},
        )
        done.raise_for_status()
        await self._settle(project_id, "archived")

    async def unarchive(self, project_id: uuid.UUID) -> None:
        done = await self.client.post(
            f"/v1/projects/{project_id}/unarchive",
            json={"version": await self._version(project_id)},
        )
        done.raise_for_status()
        await self._settle(project_id, None)

    # --- readers --------------------------------------------------------------------------

    def archive_state(self, project_id: uuid.UUID) -> str | None:
        """The project's `project_archives` state; None while it is live (no row)."""
        rows = owner_rows(
            self.db,
            "SELECT state FROM project_archives WHERE project_id = %s AND deleted_at IS NULL",
            (project_id,),
        )
        return rows[0][0] if rows else None

    def run_events(self, project_id: uuid.UUID) -> list[tuple[Any, ...]]:
        return owner_rows(
            self.db,
            "SELECT e.id, e.run_id, e.message_id, e.kind, e.payload, e.created_at, e.created_by"
            " FROM run_events e JOIN runs r ON r.id = e.run_id"
            " JOIN agent_profiles p ON p.id = r.profile_id"
            " WHERE p.project_id = %s ORDER BY e.id",
            (project_id,),
        )

    def run_events_bytes(self, project_id: uuid.UUID) -> int:
        [(size,)] = owner_rows(
            self.db,
            "SELECT coalesce(sum(octet_length(e.payload::text)), 0) FROM run_events e"
            " JOIN runs r ON r.id = e.run_id JOIN agent_profiles p ON p.id = r.profile_id"
            " WHERE p.project_id = %s",
            (project_id,),
        )
        return int(size)

    def context_items(self, project_id: uuid.UUID) -> list[tuple[Any, ...]]:
        return owner_rows(
            self.db,
            "SELECT id, owner_type, owner_id, target_type, target_url, tainted, added_by,"
            " created_at FROM context_items WHERE owner_type = 'project' AND owner_id = %s"
            " ORDER BY id",
            (project_id,),
        )

    def folder_files(self) -> list[tuple[Any, ...]]:
        return owner_rows(
            self.db,
            "SELECT id, path, size, content_hash, origin, document_id FROM folder_files"
            " WHERE location_id = %s AND deleted_at IS NULL ORDER BY path",
            (self.location_id,),
        )

    def chunks(self, project_id: uuid.UUID) -> list[tuple[Any, ...]]:
        return owner_rows(
            self.db,
            "SELECT c.id, c.document_id, c.document_version_id, c.ordinal, c.text,"
            " c.context_text, c.heading_path FROM chunks c JOIN documents d"
            " ON d.id = c.document_id WHERE d.project_id = %s ORDER BY c.id",
            (project_id,),
        )

    def add_chunks(self, project_id: uuid.UUID, n: int = 2) -> None:
        """`n` chunks for one of the project's folder documents (as extraction leaves them),
        on its current version, or its latest one while the folder sync extracts nothing."""
        [(document_id, version_id)] = owner_rows(
            self.db,
            "SELECT d.id, coalesce(d.current_version_id, v.id) FROM documents d"
            " JOIN document_versions v ON v.document_id = d.id WHERE d.project_id = %s"
            " AND d.kind = 'file' AND d.deleted_at IS NULL ORDER BY d.id, v.id DESC LIMIT 1",
            (project_id,),
        )
        for i in range(n):
            owner_exec(
                self.db,
                "INSERT INTO chunks (workspace_id, document_id, document_version_id, ordinal,"
                " text, context_text, heading_path, created_by) VALUES (%s, %s, %s, %s, %s,"
                " %s, %s, 'system')",
                (
                    self.ws.id,
                    document_id,
                    version_id,
                    i,
                    f"Paragraph {i} of the terms.",
                    f"Terms > Paragraph {i} of the terms.",
                    ["Terms"],
                ),
            )

    def blobs(
        self, project_id: uuid.UUID, *, module: str | None = None, kind: str | None = None
    ) -> list[tuple[Any, ...]]:
        """(module, kind, ref, raw_size, stored_size) of the project's archived blobs."""
        rows = owner_rows(
            self.db,
            "SELECT module, kind, ref, raw_size, stored_size FROM archived_blobs"
            " WHERE project_id = %s AND deleted_at IS NULL ORDER BY module, kind, ref",
            (project_id,),
        )
        return [
            row
            for row in rows
            if (module is None or row[0] == module) and (kind is None or row[1] == kind)
        ]

    @staticmethod
    def tree(folder: Path) -> dict[str, str]:
        """Relative path -> sha256 of every file under `folder` (links not followed)."""
        return {
            str(p.relative_to(folder)): sha256_file(p)
            for p in sorted(folder.rglob("*"))
            if p.is_file() and not p.is_symlink()
        }

    def packed_files(self, folder: Path) -> list[Path]:
        """Every file on the location outside the project's folder and the marker."""
        return [
            p
            for p in sorted(self.root.rglob("*"))
            if p.is_file() and p.name != MARKER and folder not in p.parents
        ]
