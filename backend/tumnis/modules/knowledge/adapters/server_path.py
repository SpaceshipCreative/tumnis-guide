"""`ServerPathStorage`: a location on local disk or a mounted share (P1-14, FR-15.7, SEC-5).

Every path passes `safe_rel_path`, then each existing component is checked with `lstat`:
a symlink anywhere below the root, even one pointing inside it, is refused, and files are
opened with O_NOFOLLOW so a link swapped in after the check is refused too. The root's
`.tumnis-root` marker must be present for any write: a share that is not mounted shows
the bare mount point, and Tumnis never fills the server's own disk in its place.

Writes spool to `.tumnis-tmp-<uuid>` (0600, fsynced) beside the target. A create links it
to the final name, which fails if the name exists (an atomic create-if-absent on POSIX);
on a network share without hard links it opens the final name with O_CREAT | O_EXCL
instead. A replace hashes the current file just before the rename and refuses unless it
matches `if_match`. Etags are the sha256 of the content (files are at most 50 MiB).
"""

import asyncio
import builtins
import contextlib
import errno
import hashlib
import os
import stat
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Final

from tumnis.core.adapters.base import Adapter as AdapterBase
from tumnis.core.adapters.base import AdapterRejected, AdapterUnavailable, CallPolicy
from tumnis.core.clock import Clock, SystemClock
from tumnis.modules.knowledge.rules import etag_equal, safe_rel_path
from tumnis.modules.knowledge.storage import (
    LIST_PAGE_SIZE,
    MAX_FILE_BYTES,
    FileStat,
    Health,
    LocationOffline,
    NotFound,
    Page,
    PathRejected,
    PreconditionFailed,
    StorageError,
    TooLarge,
    call_storage,
    safe_prefix,
)

CHUNK: Final = 64 * 1024
TMP_PREFIX: Final = ".tumnis-tmp-"
POLICY: Final = CallPolicy(timeout_s=120.0)  # a slow share still answers; plan default
# link() is not supported here: fall back to an exclusive open (SMB mounts, some FUSE).
_NO_LINK: Final = frozenset({errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV})
_READ_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_CREATE_FLAGS: Final = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW


class ServerPathStorage(AdapterBase):
    name: ClassVar[str] = "knowledge.server_path"
    MARKER: ClassVar[str] = ".tumnis-root"

    def __init__(
        self,
        root: Path | str,
        *,
        network_fs: bool = False,
        clock: Clock | None = None,
        policy: CallPolicy | None = None,
    ) -> None:
        super().__init__(policy=policy or POLICY, clock=clock or SystemClock())
        self.root = Path(root).resolve()
        self.network_fs = network_fs
        # (dev, inode, size, mtime_ns) -> sha256 hex, so a listing does not rehash
        # unchanged files; a replace always hashes afresh.
        self._hashes: dict[tuple[int, int, int, int], str] = {}

    # --- Paths ---------------------------------------------------------------------------

    def _resolve(self, rel: str) -> tuple[str, Path]:
        """safe_rel_path, then walk each component with os.lstat: any symlink ->
        PathRejected; a missing component ends the walk (nothing below it exists yet).
        The final path must be inside root.resolve()."""
        rel = safe_rel_path(rel)
        current = self.root
        for part in rel.split("/"):
            current = current / part
            try:
                mode = os.lstat(current).st_mode
            except (FileNotFoundError, NotADirectoryError):
                break
            if stat.S_ISLNK(mode):
                raise PathRejected(f"{rel!r}: a symlink is on the way")
        final = self.root.joinpath(*rel.split("/"))
        if not final.resolve().is_relative_to(self.root):
            raise PathRejected(f"{rel!r}: outside the location")
        return rel, final

    def _make_parents(self, final: Path) -> None:
        """Create the missing folders above `final`, checking each is a real folder."""
        current = self.root
        for part in final.relative_to(self.root).parts[:-1]:
            current = current / part
            with contextlib.suppress(FileExistsError):
                os.mkdir(current, 0o750)
            mode = os.lstat(current).st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise PathRejected(f"{final}: {part!r} is not a folder")

    def _require_marker(self) -> None:
        if not self._marker_present():
            raise LocationOffline("marker_missing")

    def _marker_present(self) -> bool:
        try:
            return stat.S_ISREG(os.lstat(self.root / self.MARKER).st_mode)
        except OSError:
            return False

    # --- Files ---------------------------------------------------------------------------

    @staticmethod
    def _open_regular(final: Path) -> tuple[int, os.stat_result] | None:
        """An fd on the regular file at `final` and its stat, or None when there is none
        (missing, or a folder). A symlink swapped in since the walk is refused (ELOOP)."""
        try:
            fd = os.open(final, _READ_FLAGS)
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            return None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise PathRejected(f"{final.name!r}: a symlink") from None
            raise
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            if stat.S_ISDIR(st.st_mode):
                return None
            raise PathRejected(f"{final.name!r}: not a regular file")
        return fd, st

    @staticmethod
    def _hash_fd(fd: int) -> str:
        digest = hashlib.sha256()
        while chunk := os.read(fd, CHUNK):
            digest.update(chunk)
        return digest.hexdigest()

    def _stat_sync(self, rel: str, final: Path, *, fresh: bool = False) -> FileStat | None:
        opened = self._open_regular(final)
        if opened is None:
            return None
        fd, st = opened
        try:
            key = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
            etag = None if fresh else self._hashes.get(key)
            if etag is None:
                etag = self._hash_fd(fd)
                self._hashes[key] = etag
        finally:
            os.close(fd)
        mtime = datetime.fromtimestamp(st.st_mtime, UTC)
        return FileStat(path=rel, size=st.st_size, mtime=mtime, etag=etag)

    async def _run[T](self, op: str, fn: Callable[[], T], *, idempotent: bool) -> T:
        """`fn` in a worker thread, through the adapter's timeout and breaker, with
        filesystem errors translated."""

        async def attempt() -> T:
            try:
                return await asyncio.to_thread(fn)
            except StorageError:
                raise
            except PermissionError as exc:
                raise AdapterRejected(self.name, op, str(exc)) from exc
            except OSError as exc:
                raise AdapterUnavailable(self.name, op, str(exc)) from exc

        return await call_storage(self, op, attempt, idempotent=idempotent)

    async def stat(self, path: str) -> FileStat | None:
        def fn() -> FileStat | None:
            rel, final = self._resolve(path)
            return self._stat_sync(rel, final)

        return await self._run("stat", fn, idempotent=True)

    async def list(self, prefix: str, cursor: str | None) -> Page[FileStat]:
        def fn() -> Page[FileStat]:
            safe = safe_prefix(prefix)
            folder = safe[:-1] if safe.endswith("/") else safe.rpartition("/")[0]
            base = self._resolve(folder)[1] if folder else self.root
            names = sorted(
                rel
                for rel in self._walk(base)
                if rel.startswith(safe) and (cursor is None or rel > cursor)
            )
            items = []
            for rel in names[:LIST_PAGE_SIZE]:
                found = self._stat_sync(rel, self.root.joinpath(*rel.split("/")))
                if found is not None:
                    items.append(found)
            more = len(names) > LIST_PAGE_SIZE
            return Page[FileStat](
                items=items, next_cursor=names[LIST_PAGE_SIZE - 1] if more else None
            )

        return await self._run("list", fn, idempotent=True)

    def _walk(self, base: Path) -> builtins.list[str]:
        """Regular files under `base` as safe relative paths; symlinks are neither listed
        nor followed, and the marker, temp files and unsafe names are left out."""
        try:
            if not stat.S_ISDIR(os.lstat(base).st_mode):
                return []
        except (FileNotFoundError, NotADirectoryError):
            return []
        found = []
        for folder, _dirs, files in os.walk(base, followlinks=False):
            for name in files:
                full = os.path.join(folder, name)
                if name.startswith(TMP_PREFIX) or not stat.S_ISREG(os.lstat(full).st_mode):
                    continue
                rel = Path(full).relative_to(self.root).as_posix()
                if rel == self.MARKER:
                    continue
                try:
                    found.append(safe_rel_path(rel))
                except PathRejected:
                    continue  # a name Tumnis cannot address; the sync engine reports it
        return found

    async def read(self, path: str) -> AsyncIterator[bytes]:
        def open_fd() -> int:
            _rel, final = self._resolve(path)
            opened = self._open_regular(final)
            if opened is None:
                raise NotFound(path)
            return opened[0]

        fd = await self._run("read", open_fd, idempotent=True)
        try:
            while chunk := await asyncio.to_thread(os.read, fd, CHUNK):
                yield chunk
        finally:
            os.close(fd)

    async def write(self, path: str, data: AsyncIterator[bytes], if_match: str | None) -> FileStat:
        async def fn() -> FileStat:
            rel, final = await asyncio.to_thread(self._prepare, path)
            tmp, size, etag = await self._spool_tmp(final, data)
            try:
                await asyncio.to_thread(self._commit, rel, final, tmp, if_match)
            finally:
                await asyncio.to_thread(_unlink_quietly, tmp)
            mtime = datetime.fromtimestamp(os.lstat(final).st_mtime, UTC)
            return FileStat(path=rel, size=size, mtime=mtime, etag=etag)

        async def attempt() -> FileStat:
            try:
                return await fn()
            except StorageError:
                raise
            except PermissionError as exc:
                raise AdapterRejected(self.name, "write", str(exc)) from exc
            except OSError as exc:
                raise AdapterUnavailable(self.name, "write", str(exc)) from exc

        return await call_storage(self, "write", attempt, idempotent=False)

    def _prepare(self, path: str) -> tuple[str, Path]:
        rel, final = self._resolve(path)
        self._require_marker()
        self._make_parents(final)
        return self._resolve(rel)  # again, now that the folders exist

    async def _spool_tmp(self, final: Path, data: AsyncIterator[bytes]) -> tuple[Path, int, str]:
        tmp = final.with_name(f"{TMP_PREFIX}{uuid.uuid4().hex}")
        fd = await asyncio.to_thread(os.open, tmp, _CREATE_FLAGS, 0o600)
        digest, size = hashlib.sha256(), 0
        try:
            async for chunk in data:
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise TooLarge(f"over {MAX_FILE_BYTES} bytes")
                digest.update(chunk)
                await asyncio.to_thread(_write_all, fd, chunk)
            await asyncio.to_thread(os.fsync, fd)
        except BaseException:
            os.close(fd)
            await asyncio.to_thread(_unlink_quietly, tmp)
            raise
        os.close(fd)
        return tmp, size, digest.hexdigest()

    def _commit(self, rel: str, final: Path, tmp: Path, if_match: str | None) -> None:
        if if_match is None:
            self._create(rel, final, tmp)
        else:
            current = self._stat_sync(rel, final, fresh=True)  # hashed just before the rename
            if current is None or not etag_equal(current.etag, if_match):
                raise PreconditionFailed(current)
            os.replace(tmp, final)
        _fsync_dir(final.parent)

    def _create(self, rel: str, final: Path, tmp: Path) -> None:
        if not self.network_fs:
            try:
                os.link(tmp, final, follow_symlinks=False)
                return
            except FileExistsError:
                raise PreconditionFailed(self._stat_sync(rel, final)) from None
            except OSError as exc:
                if exc.errno not in _NO_LINK:
                    raise
        try:
            out = os.open(final, _CREATE_FLAGS, 0o600)
        except FileExistsError:
            raise PreconditionFailed(self._stat_sync(rel, final)) from None
        try:
            with open(tmp, "rb") as src:
                while chunk := src.read(CHUNK):
                    _write_all(out, chunk)
            os.fsync(out)
        finally:
            os.close(out)

    async def move(self, src: str, dst: str) -> None:
        def fn() -> None:
            src_rel, src_final = self._resolve(src)
            dst_rel, dst_final = self._resolve(dst)
            try:
                mode = os.lstat(src_final).st_mode
            except (FileNotFoundError, NotADirectoryError):
                raise NotFound(src_rel) from None
            if not stat.S_ISREG(mode):
                raise NotFound(src_rel)
            self._require_marker()
            self._make_parents(dst_final)
            dst_rel, dst_final = self._resolve(dst_rel)
            self._move_no_clobber(src_final, dst_rel, dst_final)

        await self._run("move", fn, idempotent=False)

    def _move_no_clobber(self, src_final: Path, dst_rel: str, dst_final: Path) -> None:
        """link + unlink: the link fails if the destination exists. Without hard links the
        destination is checked just before a rename (a narrow race on such shares)."""
        try:
            os.link(src_final, dst_final, follow_symlinks=False)
        except FileExistsError:
            raise PreconditionFailed(self._stat_sync(dst_rel, dst_final)) from None
        except OSError as exc:
            if exc.errno not in _NO_LINK:
                raise
            if os.path.lexists(dst_final):
                raise PreconditionFailed(self._stat_sync(dst_rel, dst_final)) from None
            os.rename(src_final, dst_final)
        else:
            os.unlink(src_final)
        _fsync_dir(dst_final.parent)

    async def delete(self, path: str) -> None:
        def fn() -> None:
            _rel, final = self._resolve(path)
            try:
                mode = os.lstat(final).st_mode
            except (FileNotFoundError, NotADirectoryError):
                return
            if stat.S_ISREG(mode):
                _unlink_quietly(final)

        await self._run("delete", fn, idempotent=True)

    async def health(self) -> Health:
        present = await asyncio.to_thread(self._marker_present)
        return Health.ok() if present else Health.degraded("marker_missing")


def _write_all(fd: int, chunk: bytes) -> None:
    view = memoryview(chunk)
    while view:
        view = view[os.write(fd, view) :]


def _unlink_quietly(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)


def _fsync_dir(folder: Path) -> None:
    """Make a new name durable; some shares cannot open a folder for fsync."""
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
