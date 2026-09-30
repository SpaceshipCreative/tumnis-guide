"""The folder-sync scenario runner (P1-15, FR-15.12). No assertions about the product live
outside `expect`: each scenario in `backend/fixtures/folder_sync/*.yaml` is a script of
operations on one project folder, on a server path or on MinIO, then expectations.

Operations (paths are relative to the project folder; this is the vocabulary P3-14's
scenarios reuse):

- `outside_write {path, content | append}`, `outside_rename {from, to}`,
  `outside_delete {path}`, `outside_touch {path}`: a change made outside Tumnis, straight
  on the disk or the bucket.
- `unmount` / `remount` (server path, share): the share drops and the bare mount point
  shows (the marker and every file gone), then comes back. On SFTP (P3-14) the location's
  root goes away and comes back.

P3-14 runs the same scenarios on a share (`share`, a server path of that kind) and on the
SFTP container (`sftp`), and can set the project up in an existing folder (`mode`).
- `tumnis_create_note {title, body}`, `tumnis_edit_note {title, body}`,
  `tumnis_save_note {title, status}`, `tumnis_delete {title | path}`,
  `tumnis_edit_document {path, body, error}`, `force_document_body {path, body}`,
  `upload {name, content}`: what Tumnis does (through `knowledge.api`).
- `remember {as, path | title}`: keep a Document's id to compare later.
- `sync`: one `folder_sync` run of the location, as the worker runs it.
- `expect {...}`: see `FolderRunner.expect`.
"""

from __future__ import annotations

import inspect
import os
import posixpath
import shutil
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER
from tumnis.core.net import NetPolicy

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests._services import S3Endpoint, SftpEndpoint
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

SELF_HOSTED = NetPolicy(mode="self-hosted")
MARKER = ".tumnis-root"
S3_PREFIX = "tumnis"
Backend = str  # "server_path" | "s3" | "share" | "sftp" (P3-14)
ON_DISK = frozenset({"server_path", "share"})  # a share is a server path of kind "share"


async def chunks(data: bytes) -> AsyncIterator[bytes]:
    yield data


class RecordingStorage:
    """A storage backend that records every write that landed (create or replace), every
    delete and (P3-14) every move, then does what the backend it wraps does."""

    def __init__(
        self,
        inner: Any,
        log: list[tuple[str, str]],
        moves: list[tuple[str, str]] | None = None,
    ) -> None:
        self._inner = inner
        self._log = log
        self._moves = moves if moves is not None else []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def write(self, path: str, data: AsyncIterator[bytes], if_match: str | None) -> Any:
        written = await self._inner.write(path, data, if_match)
        self._log.append(("create" if if_match is None else "replace", path))
        return written

    async def delete(self, path: str) -> None:
        await self._inner.delete(path)
        self._log.append(("delete", path))

    async def move(self, src: str, dst: str) -> None:
        await self._inner.move(src, dst)
        self._moves.append((src, dst))


@dataclass
class FolderRunner:
    """One scenario's world: a workspace, one location (server path or MinIO bucket), one
    project folder, and what Tumnis did to it."""

    db: DbUrls
    ws: WorkspaceHandle
    clock: FixedClock
    backend: Backend
    root: Path | None = None  # server path (or share) location root
    minio: S3Endpoint | None = None
    bucket: str | None = None
    sftp: SftpEndpoint | None = None  # P3-14
    sftp_root: str = ""  # the SFTP location's root as the container's user sees it
    mode: str = "tumnis_made"  # P3-14: "existing" sets the folder up in an existing folder
    existing_path: str = ""  # that folder, relative to the location's root
    location_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    folder_root: str = ""
    storage_log: list[tuple[str, str]] = field(default_factory=list)
    move_log: list[tuple[str, str]] = field(default_factory=list)  # (from, to), P3-14
    extraction_log: list[str] = field(default_factory=list)
    remembered: dict[str, uuid.UUID] = field(default_factory=dict)
    _restore: list[Callable[[], Any]] = field(default_factory=list)

    # --- Setup ---------------------------------------------------------------------------

    async def start(self, project: str = "acme-site") -> None:
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
        from tumnis.modules.knowledge import sync  # noqa: PLC0415
        from tumnis.modules.projects import api as projects  # noqa: PLC0415

        sync.configure(net=SELF_HOSTED)
        self._restore.append(lambda: sync.configure(net=None))
        previous = sync.use(clock=self.clock, extraction=self._record_extraction)
        self._restore.append(lambda: sync.use(clock=previous[0], extraction=previous[1]))
        knowledge.use_backend_hook(
            lambda _row, built: RecordingStorage(built, self.storage_log, self.move_log)
        )
        self._restore.append(lambda: knowledge.use_backend_hook(None))

        if self.backend == "sftp":
            from tumnis.modules.knowledge.tests.integration._locations import (  # noqa: PLC0415
                pinned_sftp_location,
            )

            assert self.sftp is not None
            location, self.sftp_root = await pinned_sftp_location(self.ws, self.sftp, default=True)
        else:
            async with tenant_session(self.ws.ctx) as s:
                location = await knowledge.create_location(
                    s, await self._location_in(), net=SELF_HOSTED
                )
        assert location.status == "online", location
        self.location_id = location.id
        async with tenant_session(self.ws.ctx) as s:
            created = await projects.create_project(
                s, self.ws.ctx.actor, projects.ProjectCreate(name=project), now=self.clock.now()
            )
            self.project_id = created.id
            if self.mode == "existing":
                folder = await knowledge.use_existing_folder(
                    s, created.id, location_id=location.id, path=self.existing_path,
                    net=SELF_HOSTED,
                )  # fmt: skip
            else:
                folder = await knowledge.ensure_project_folder(s, created.id, net=SELF_HOSTED)
        assert folder is not None
        self.folder_root = folder.root_path
        self.storage_log.clear()
        self.move_log.clear()

    async def _location_in(self) -> Any:
        from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

        if self.backend in ON_DISK:
            assert self.root is not None
            return knowledge.LocationIn(
                name="disk", kind=self.backend, root=str(self.root), is_default=True
            )
        from tumnis.modules.knowledge.tests.contract.test_storage_s3 import (  # noqa: PLC0415
            make_bucket,
        )
        from tumnis.modules.knowledge.tests.integration.test_locations import (  # noqa: PLC0415
            _lan_endpoint,
        )

        assert self.minio is not None
        self.bucket = await make_bucket(self.minio)
        return knowledge.LocationIn(
            name="minio",
            kind="s3",
            root=f"{self.bucket}/{S3_PREFIX}",
            is_default=True,
            s3=knowledge.S3ConfigIn(
                endpoint=_lan_endpoint(self.minio),
                region=self.minio.region,
                access_key=self.minio.access_key,
                secret_key=self.minio.secret_key,
            ),
        )

    async def close(self) -> None:
        for undo in reversed(self._restore):
            undo()
        self._restore.clear()

    async def _record_extraction(
        self, _workspace_id: uuid.UUID, _version_id: uuid.UUID, path: str
    ) -> None:
        found = self._in_folder(path)
        assert found is not None, path
        self.extraction_log.append(found)

    # --- Paths -----------------------------------------------------------------------------

    def _rel(self, path: str) -> str:
        """A folder path as the location sees it."""
        return f"{self.folder_root}/{path}"

    def _in_folder(self, location_path: str) -> str | None:
        prefix = self.folder_root + "/"
        return location_path[len(prefix) :] if location_path.startswith(prefix) else None

    def _disk(self, path: str) -> Path:
        assert self.root is not None
        return self.root / self.folder_root / path

    def _key(self, path: str) -> str:
        return f"{S3_PREFIX}/{self._rel(path)}"

    def _s3(self) -> Any:
        from tumnis.modules.knowledge.tests.contract.test_storage_s3 import (  # noqa: PLC0415
            _raw_client,
        )

        assert self.minio is not None
        return _raw_client(self.minio)

    # --- Outside -----------------------------------------------------------------------------

    def _sftp_path(self, path: str) -> str:
        """A folder path as the SFTP container's user sees it."""
        return posixpath.join(self.sftp_root, self.folder_root, *([path] if path else []))

    def _raw_sftp(self) -> Any:
        from tumnis.modules.knowledge.tests._sftp import raw_sftp  # noqa: PLC0415

        assert self.sftp is not None
        return raw_sftp(self.sftp)

    async def read_outside(self, path: str) -> bytes | None:
        if self.backend in ON_DISK:
            disk = self._disk(path)
            return disk.read_bytes() if disk.is_file() else None
        if self.backend == "sftp":
            async with self._raw_sftp() as sftp:
                if not await sftp.isfile(self._sftp_path(path)):
                    return None
                async with sftp.open(self._sftp_path(path), "rb") as handle:
                    held: bytes = await handle.read()
                    return held
        async with self._s3() as s3:
            try:
                got = await s3.get_object(Bucket=self.bucket, Key=self._key(path))
            except s3.exceptions.NoSuchKey:
                return None
            async with got["Body"] as body:
                data: bytes = await body.read()
                return data

    async def outside_write(
        self, path: str, content: str | None = None, append: str | None = None
    ) -> None:
        if append is not None:
            current = await self.read_outside(path)
            assert current is not None, f"nothing to append to at {path}"
            data = current + append.encode()
        else:
            assert content is not None
            data = content.encode()
        if self.backend in ON_DISK:
            disk = self._disk(path)
            disk.parent.mkdir(parents=True, exist_ok=True)
            disk.write_bytes(data)
            return
        if self.backend == "sftp":
            target = self._sftp_path(path)
            async with self._raw_sftp() as sftp:
                await sftp.makedirs(posixpath.dirname(target), exist_ok=True)
                async with sftp.open(target, "wb") as handle:
                    await handle.write(data)
            return
        async with self._s3() as s3:
            await s3.put_object(Bucket=self.bucket, Key=self._key(path), Body=data)

    async def outside_rename(self, src: str, dst: str) -> None:
        if self.backend in ON_DISK:
            self._disk(dst).parent.mkdir(parents=True, exist_ok=True)
            os.rename(self._disk(src), self._disk(dst))
            return
        if self.backend == "sftp":
            async with self._raw_sftp() as sftp:
                await sftp.makedirs(posixpath.dirname(self._sftp_path(dst)), exist_ok=True)
                await sftp.rename(self._sftp_path(src), self._sftp_path(dst))
            return
        async with self._s3() as s3:
            await s3.copy_object(
                Bucket=self.bucket,
                Key=self._key(dst),
                CopySource={"Bucket": self.bucket, "Key": self._key(src)},
            )
            await s3.delete_object(Bucket=self.bucket, Key=self._key(src))

    async def outside_delete(self, path: str) -> None:
        if self.backend in ON_DISK:
            self._disk(path).unlink()
            return
        if self.backend == "sftp":
            async with self._raw_sftp() as sftp:
                await sftp.remove(self._sftp_path(path))
            return
        async with self._s3() as s3:
            await s3.delete_object(Bucket=self.bucket, Key=self._key(path))

    async def outside_touch(self, path: str) -> None:
        assert self.backend != "s3", "S3 has no touch"
        five_minutes = int(timedelta(minutes=5).total_seconds())
        if self.backend == "sftp":
            async with self._raw_sftp() as sftp:
                attrs = await sftp.stat(self._sftp_path(path))
                await sftp.utime(self._sftp_path(path), (attrs.atime, attrs.mtime + five_minutes))
            return
        disk = self._disk(path)
        st = disk.stat()
        later = st.st_mtime_ns + five_minutes * 10**9
        os.utime(disk, ns=(st.st_atime_ns, later))

    async def unmount(self) -> None:
        if self.backend == "sftp":
            async with self._raw_sftp() as sftp:
                await sftp.rename(self.sftp_root, self.sftp_root + ".away")
            return
        assert self.root is not None
        assert self.backend in ON_DISK
        os.rename(self.root, self.root.with_name(self.root.name + ".share"))
        self.root.mkdir()

    async def remount(self) -> None:
        if self.backend == "sftp":
            async with self._raw_sftp() as sftp:
                await sftp.rename(self.sftp_root + ".away", self.sftp_root)
            return
        assert self.root is not None
        assert self.backend in ON_DISK
        self.root.rmdir()  # the bare mount point must still be empty
        os.rename(self.root.with_name(self.root.name + ".share"), self.root)

    def _tumnis_dir(self) -> str:
        """Where Tumnis keeps its own files in the folder: `.tumnis/`, or `Tumnis/` in an
        existing folder (P3-14)."""
        return "Tumnis/" if self.mode == "existing" else ".tumnis/"

    async def listing(self) -> list[str]:
        """Every file in the project folder but Tumnis's own `.tumnis/` (in an existing
        folder, Tumnis's trash `Tumnis/.trash/`)."""
        hidden = "Tumnis/.trash/" if self.mode == "existing" else ".tumnis/"
        return sorted(p for p in await self._all_files() if not p.startswith(hidden))

    async def trash(self) -> list[str]:
        prefix = "Tumnis/.trash/" if self.mode == "existing" else ".tumnis/trash/"
        return sorted(p[len(prefix) :] for p in await self._all_files() if p.startswith(prefix))

    async def mount_point_files(self) -> list[str]:
        """Every entry under the location's root (the bare mount point after `unmount`)."""
        if self.backend == "sftp":
            async with self._raw_sftp() as sftp:
                if not await sftp.isdir(self.sftp_root):
                    return []
                return sorted(await _sftp_walk(sftp, self.sftp_root, dirs=True))
        assert self.root is not None
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))

    async def file_mtime(self, path: str) -> Any:
        """The file's mtime as the backend reports it (whole seconds on SFTP)."""
        from datetime import UTC, datetime  # noqa: PLC0415

        if self.backend == "sftp":
            async with self._raw_sftp() as sftp:
                attrs = await sftp.stat(self._sftp_path(path))
            return datetime.fromtimestamp(attrs.mtime, UTC)
        return datetime.fromtimestamp(self._disk(path).stat().st_mtime, UTC)

    async def _all_files(self) -> list[str]:
        if self.backend in ON_DISK:
            base = self._disk("")
            return [str(p.relative_to(base)) for p in base.rglob("*") if p.is_file()]
        if self.backend == "sftp":
            async with self._raw_sftp() as sftp:
                folder = self._sftp_path("")
                if not await sftp.isdir(folder):
                    return []
                return await _sftp_walk(sftp, folder, dirs=False)
        found: list[str] = []
        prefix = self._key("")
        async with self._s3() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                found += [obj["Key"][len(prefix) :] for obj in page.get("Contents", [])]
        return found

    # --- Tumnis ------------------------------------------------------------------------------

    async def tumnis_create_note(self, title: str, body: str, project: str | None = None) -> None:
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

        del project  # one project per scenario
        async with tenant_session(self.ws.ctx) as s:
            await knowledge.put_text_document(
                s, self.project_id, title=title, body_md=body, role=None
            )

    async def tumnis_edit_note(self, title: str, body: str) -> None:
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

        doc_id, version = self._doc_by_title(title)
        async with tenant_session(self.ws.ctx) as s:
            await knowledge.update_text_document(s, doc_id, body_md=body, version=version)

    async def tumnis_save_note(self, title: str, status: str) -> None:
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

        doc_id, _version = self._doc_by_title(title)
        async with tenant_session(self.ws.ctx) as s:
            saved = await knowledge.save_note(s, doc_id, net=SELF_HOSTED)
        assert saved.status == status, saved

    async def tumnis_delete(self, title: str | None = None, path: str | None = None) -> None:
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

        doc_id = self._doc_by_title(title)[0] if title else self._doc_by_path(path or "")[0]
        async with tenant_session(self.ws.ctx) as s:
            await knowledge.trash_document(s, doc_id)

    async def tumnis_edit_document(self, path: str, body: str, error: str) -> None:
        from tumnis.core.errors import ProblemError  # noqa: PLC0415
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

        doc_id, version = self._doc_by_path(path)
        with pytest.raises(ProblemError) as refused:
            async with tenant_session(self.ws.ctx) as s:
                await knowledge.update_text_document(s, doc_id, body_md=body, version=version)
        assert refused.value.code == error, refused.value

    def force_document_body(self, path: str, body: str) -> None:
        """A state the UI never makes: the outside file's Document edited in Tumnis."""
        import hashlib  # noqa: PLC0415

        doc_id, _version = self._doc_by_path(path)
        with psycopg.connect(self.db.libpq(OWNER)) as conn:
            conn.execute(
                "UPDATE documents SET body_md = %s, content_hash = %s WHERE id = %s",
                (body, hashlib.sha256(body.encode()).digest(), doc_id),
            )

    async def upload(self, name: str, content: str) -> None:
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

        async with tenant_session(self.ws.ctx) as s:
            await knowledge.place_upload(
                s,
                self.project_id,  # type: ignore[arg-type]  # set by the scenario's setup
                name,
                chunks(content.encode()),
                net=SELF_HOSTED,
            )

    def remember(self, name: str, path: str | None = None, title: str | None = None) -> None:
        self.remembered[name] = (
            self._doc_by_title(title)[0] if title else self._doc_by_path(path or "")[0]
        )

    async def sync(self) -> Any:
        from dbos import SetWorkflowID  # noqa: PLC0415

        from tumnis.modules.knowledge import workflows  # noqa: PLC0415

        with SetWorkflowID(f"scenario-sync-{uuid.uuid4()}"):
            return await workflows.folder_sync(str(self.ws.id), str(self.location_id))

    # --- Database reads ------------------------------------------------------------------

    def _rows(self, sql: str, *params: object) -> list[tuple[Any, ...]]:
        with psycopg.connect(self.db.libpq(OWNER)) as conn:
            return conn.execute(sql, params).fetchall()

    def _doc_by_title(self, title: str) -> tuple[uuid.UUID, int]:
        rows = self._rows(
            "SELECT id, version FROM documents WHERE project_id = %s AND title = %s"
            " AND deleted_at IS NULL",
            self.project_id,
            title,
        )
        assert len(rows) == 1, (title, rows)
        return rows[0][0], rows[0][1]

    def _doc_by_path(self, path: str) -> tuple[uuid.UUID, int]:
        rows = self._rows(
            "SELECT d.id, d.version FROM folder_files f JOIN documents d ON d.id = f.document_id"
            " WHERE f.location_id = %s AND f.path = %s AND f.deleted_at IS NULL",
            self.location_id,
            self._rel(path),
        )
        assert len(rows) == 1, (path, rows)
        return rows[0][0], rows[0][1]

    # --- Expectations --------------------------------------------------------------------

    async def expect(self, spec: dict[str, Any]) -> None:  # noqa: PLR0912  # one branch per key
        for key, want in spec.items():
            if key == "files":
                for path, checks in want.items():
                    await self._expect_file(path, checks)
            elif key == "listing":
                assert await self.listing() == sorted(want), key
            elif key == "trash":
                assert await self.trash() == sorted(want), key
            elif key == "documents":
                assert self._count_docs(trashed=False) == want["count"], key
            elif key == "trashed_documents":
                assert self._count_docs(trashed=True) == want["count"], key
            elif key == "document":
                self._expect_document(dict(want))
            elif key == "review_items":
                kinds = sorted(
                    row[0]
                    for row in self._rows(
                        "SELECT kind FROM review_items WHERE decided_at IS NULL"
                        " AND deleted_at IS NULL"
                    )
                )
                assert kinds == sorted(item["kind"] for item in want), key
            elif key == "extraction_requested":
                assert Counter(self.extraction_log) == Counter(want), (key, self.extraction_log)
            elif key == "location":
                (status,) = self._rows(
                    "SELECT status FROM storage_locations WHERE id = %s", self.location_id
                )[0]
                assert status == want["status"], key
            elif key == "pending_writes":
                (count,) = self._rows("SELECT count(*) FROM pending_writes")[0]
                assert count == want, key
            elif key == "mount_point_files":
                assert await self.mount_point_files() == want, key
            elif key == "record":
                await self._expect_record(want)
            elif key == "writes":
                for path, kinds in want.items():
                    done = [
                        k for k, p in self.storage_log if p == self._rel(path) and k != "delete"
                    ]
                    assert done == kinds, (key, path, self.storage_log)
            elif key == "deletes":
                assert sum(1 for k, _p in self.storage_log if k == "delete") == want, key
            else:
                raise AssertionError(f"unknown expectation {key!r}")

    async def _expect_file(self, path: str, checks: dict[str, Any]) -> None:
        data = await self.read_outside(path)
        assert data is not None, f"no file at {path}"
        text = data.decode()
        if "contains" in checks:
            assert checks["contains"] in text, (path, text)
        if "not_contains" in checks:
            assert checks["not_contains"] not in text, (path, text)
        if "equals" in checks:
            assert text == checks["equals"], (path, text)
        if "tumnis_id_of" in checks:
            doc_id, _version = self._doc_by_title(checks["tumnis_id_of"])
            assert text.startswith(f"---\ntumnis_id: {doc_id}\n---\n"), (path, text)

    def _count_docs(self, *, trashed: bool) -> int:
        state = "IS NOT NULL" if trashed else "IS NULL"
        (count,) = self._rows(
            f"SELECT count(*) FROM documents WHERE project_id = %s AND deleted_at {state}",  # noqa: S608
            self.project_id,
        )[0]
        return int(count)

    def _expect_document(self, want: dict[str, Any]) -> None:
        doc_id, _version = self._doc_by_path(want.pop("path"))
        (tainted, trust, body) = self._rows(
            "SELECT tainted, trust, body_md FROM documents WHERE id = %s", doc_id
        )[0]
        (versions,) = self._rows(
            "SELECT count(*) FROM document_versions WHERE document_id = %s", doc_id
        )[0]
        for key, value in want.items():
            if key == "id":
                assert doc_id == self.remembered[value], key
            elif key == "versions":
                assert versions == value, (key, versions)
            elif key == "tainted":
                assert tainted is value, key
            elif key == "trust":
                assert trust == value, key
            elif key == "body":
                assert body == value, (key, body)
            elif key == "body_contains":
                assert value in (body or ""), (key, body)
            else:
                raise AssertionError(f"unknown document expectation {key!r}")

    async def _expect_record(self, want: dict[str, Any]) -> None:
        rows = self._rows(
            "SELECT mtime FROM folder_files WHERE location_id = %s AND path = %s"
            " AND deleted_at IS NULL",
            self.location_id,
            self._rel(want["path"]),
        )
        assert len(rows) == 1, rows
        if want.get("mtime_matches_file"):
            assert rows[0][0] == await self.file_mtime(want["path"])

    # --- The script ------------------------------------------------------------------------

    async def run(self, steps: list[Any]) -> None:
        for number, step in enumerate(steps, start=1):
            name, args = (step, {}) if isinstance(step, str) else next(iter(step.items()))
            try:
                await self._step(name, args or {})
            except AssertionError as exc:
                raise AssertionError(f"step {number} ({name}): {exc}") from exc

    async def _step(self, name: str, args: dict[str, Any]) -> None:
        if name == "sync":
            await self.sync()
            return
        if name == "expect":
            await self.expect(args)
            return
        if name == "outside_rename":
            await self.outside_rename(args["from"], args["to"])
            return
        if name == "remember":
            self.remember(args["as"], path=args.get("path"), title=args.get("title"))
            return
        if name == "force_document_body":
            self.force_document_body(**args)
            return
        done = getattr(self, name)(**args)
        if inspect.isawaitable(done):
            await done


def load_scenarios(folder: Path) -> list[tuple[str, Backend, dict[str, Any]]]:
    """(scenario id, backend, scenario) for every scenario and each backend it names."""
    import yaml  # noqa: PLC0415

    found = []
    for path in sorted(folder.glob("*.yaml")):
        scenario = yaml.safe_load(path.read_text())
        assert scenario["id"] == path.stem, path
        found += [(scenario["id"], backend, scenario) for backend in scenario["backends"]]
    return found


async def _sftp_walk(sftp: Any, base: str, *, dirs: bool) -> list[str]:
    """Every file (and, with `dirs`, every folder) under `base` on the SFTP server,
    relative to it; symlinks are not followed."""
    import stat  # noqa: PLC0415

    found: list[str] = []
    pending = [""]
    while pending:
        rel = pending.pop()
        for entry in await sftp.readdir(posixpath.join(base, rel) if rel else base):
            if entry.filename in {".", ".."}:
                continue
            child = posixpath.join(rel, entry.filename) if rel else entry.filename
            mode = entry.attrs.permissions or 0
            if stat.S_ISDIR(mode):
                pending.append(child)
                if dirs:
                    found.append(child)
            elif stat.S_ISREG(mode):
                found.append(child)
    return found


def make_root(root: Path) -> Path:
    root.mkdir()
    (root / MARKER).write_text("tumnis\n")
    return root


def remove_share(root: Path) -> None:
    shutil.rmtree(root.with_name(root.name + ".share"), ignore_errors=True)
