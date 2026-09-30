"""`ServerPathStorage`: a location on local disk or a mounted share (P1-14, FR-15.7, SEC-5).

Every path passes `safe_rel_path`, then each existing component is checked with `lstat`:
a symlink anywhere below the root, even one pointing inside it, is refused. The operations
then go by fd: each folder is opened from its parent's fd with O_DIRECTORY | O_NOFOLLOW and
files are opened, linked, renamed and unlinked relative to their folder's fd, so a folder or
file swapped for a symlink after the check is refused too. The root's
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
import posixpath
import stat
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
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
_DIR_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


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

    def _resolve(self, rel: str) -> str:
        """safe_rel_path, then walk each component with os.lstat: any symlink ->
        PathRejected; a missing component ends the walk (nothing below it exists yet).
        The final path must be inside root.resolve(). This check gives the clear refusal;
        the operations themselves then go by folder fd (`_open_dir`), so a swap after it
        cannot lead outside the root either."""
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
        return rel

    def _open_dir(self, parts: Sequence[str], *, create: bool = False) -> int:
        """An fd on root/<parts>, each folder opened from its parent's fd with O_DIRECTORY |
        O_NOFOLLOW, so a folder swapped for a symlink after `_resolve` cannot lead outside
        the root. A missing folder raises FileNotFoundError (or is made, with `create`);
        a file on the way raises NotADirectoryError (PathRejected with `create`)."""
        fd = os.open(self.root, _DIR_FLAGS)
        try:
            for part in parts:
                if create:
                    with contextlib.suppress(FileExistsError):
                        os.mkdir(part, 0o750, dir_fd=fd)
                try:
                    child = os.open(part, _DIR_FLAGS, dir_fd=fd)
                except NotADirectoryError:
                    # O_DIRECTORY | O_NOFOLLOW says ENOTDIR for a symlink and a file alike.
                    mode = os.stat(part, dir_fd=fd, follow_symlinks=False).st_mode
                    if stat.S_ISLNK(mode):
                        raise PathRejected(f"{part!r}: a symlink is on the way") from None
                    if create:
                        raise PathRejected(f"{part!r} is not a folder") from None
                    raise
                except OSError as exc:
                    if exc.errno == errno.ELOOP:
                        raise PathRejected(f"{part!r}: a symlink is on the way") from None
                    raise
                os.close(fd)
                fd = child
        except BaseException:
            os.close(fd)
            raise
        return fd

    @contextlib.contextmanager
    def _parent(self, rel: str, *, create: bool = False) -> Iterator[tuple[int, str]]:
        """(fd on the folder holding `rel`, the file's name in it); see `_open_dir`."""
        *folders, name = rel.split("/")
        fd = self._open_dir(folders, create=create)
        try:
            yield fd, name
        finally:
            os.close(fd)

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
    def _open_regular(dir_fd: int, name: str) -> tuple[int, os.stat_result] | None:
        """An fd on the regular file `name` in `dir_fd` and its stat, or None when there is
        none (missing, or a folder). A symlink swapped in since the walk is refused (ELOOP)."""
        try:
            fd = os.open(name, _READ_FLAGS, dir_fd=dir_fd)
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            return None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise PathRejected(f"{name!r}: a symlink") from None
            raise
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            if stat.S_ISDIR(st.st_mode):
                return None
            raise PathRejected(f"{name!r}: not a regular file")
        return fd, st

    @staticmethod
    def _hash_fd(fd: int) -> str:
        digest = hashlib.sha256()
        while chunk := os.read(fd, CHUNK):
            digest.update(chunk)
        return digest.hexdigest()

    def _stat_at(self, rel: str, dir_fd: int, name: str, *, fresh: bool = False) -> FileStat | None:
        opened = self._open_regular(dir_fd, name)
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

    def _stat_sync(self, rel: str) -> FileStat | None:
        try:
            with self._parent(rel) as (dir_fd, name):
                return self._stat_at(rel, dir_fd, name)
        except (FileNotFoundError, NotADirectoryError):
            return None

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
            return self._stat_sync(self._resolve(path))

        return await self._run("stat", fn, idempotent=True)

    async def list(self, prefix: str, cursor: str | None) -> Page[FileStat]:
        def fn() -> Page[FileStat]:
            safe = safe_prefix(prefix)
            folder = safe[:-1] if safe.endswith("/") else safe.rpartition("/")[0]
            if folder:
                folder = self._resolve(folder)
            names = sorted(
                rel
                for rel in self._walk(folder)
                if rel.startswith(safe) and (cursor is None or rel > cursor)
            )
            items = []
            for rel in names[:LIST_PAGE_SIZE]:
                found = self._stat_sync(rel)
                if found is not None:
                    items.append(found)
            more = len(names) > LIST_PAGE_SIZE
            return Page[FileStat](
                items=items, next_cursor=names[LIST_PAGE_SIZE - 1] if more else None
            )

        return await self._run("list", fn, idempotent=True)

    def _walk(self, folder: str) -> builtins.list[str]:
        """Regular files under root/`folder` as safe relative paths, walked by fd; symlinks
        are neither listed nor followed, and the marker, temp files and unsafe names are
        left out."""
        try:
            base_fd = self._open_dir(folder.split("/") if folder else [])
        except (FileNotFoundError, NotADirectoryError):
            return []
        found = []
        try:
            for here, _dirs, files, here_fd in os.fwalk(".", dir_fd=base_fd, follow_symlinks=False):
                for name in files:
                    try:
                        mode = os.stat(name, dir_fd=here_fd, follow_symlinks=False).st_mode
                    except FileNotFoundError:
                        continue
                    if name.startswith(TMP_PREFIX) or not stat.S_ISREG(mode):
                        continue
                    rel = posixpath.normpath(posixpath.join(folder, here, name))
                    if rel == self.MARKER:
                        continue
                    try:
                        found.append(safe_rel_path(rel))
                    except PathRejected:
                        continue  # a name Tumnis cannot address; the sync engine reports it
        finally:
            os.close(base_fd)
        return found

    async def read(self, path: str) -> AsyncIterator[bytes]:
        def open_fd() -> int:
            rel = self._resolve(path)
            try:
                with self._parent(rel) as (dir_fd, name):
                    opened = self._open_regular(dir_fd, name)
            except (FileNotFoundError, NotADirectoryError):
                opened = None
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
            rel, dir_fd, name = await asyncio.to_thread(self._prepare, path)
            try:
                tmp, size, etag = await self._spool_tmp(dir_fd, data)
                try:
                    await asyncio.to_thread(self._commit, rel, dir_fd, name, tmp, if_match)
                finally:
                    await asyncio.to_thread(_unlink_quietly, dir_fd, tmp)
                st = await asyncio.to_thread(os.stat, name, dir_fd=dir_fd, follow_symlinks=False)
            finally:
                os.close(dir_fd)
            mtime = datetime.fromtimestamp(st.st_mtime, UTC)
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

    def _prepare(self, path: str) -> tuple[str, int, str]:
        """(rel, fd on the target's folder, made if missing, the target's name)."""
        rel = self._resolve(path)
        self._require_marker()
        *folders, name = rel.split("/")
        return rel, self._open_dir(folders, create=True), name

    async def _spool_tmp(self, dir_fd: int, data: AsyncIterator[bytes]) -> tuple[str, int, str]:
        tmp = f"{TMP_PREFIX}{uuid.uuid4().hex}"
        fd = await asyncio.to_thread(os.open, tmp, _CREATE_FLAGS, 0o600, dir_fd=dir_fd)
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
            await asyncio.to_thread(_unlink_quietly, dir_fd, tmp)
            raise
        os.close(fd)
        return tmp, size, digest.hexdigest()

    def _commit(self, rel: str, dir_fd: int, name: str, tmp: str, if_match: str | None) -> None:
        if if_match is None:
            self._create(rel, dir_fd, name, tmp)
        else:
            current = self._stat_at(rel, dir_fd, name, fresh=True)  # hashed just before
            if current is None or not etag_equal(current.etag, if_match):
                raise PreconditionFailed(current)
            os.replace(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        _fsync_dir(dir_fd)

    def _create(self, rel: str, dir_fd: int, name: str, tmp: str) -> None:
        if not self.network_fs:
            try:
                os.link(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
                return
            except FileExistsError:
                raise PreconditionFailed(self._stat_at(rel, dir_fd, name)) from None
            except OSError as exc:
                if exc.errno not in _NO_LINK:
                    raise
        try:
            out = os.open(name, _CREATE_FLAGS, 0o600, dir_fd=dir_fd)
        except FileExistsError:
            raise PreconditionFailed(self._stat_at(rel, dir_fd, name)) from None
        try:
            try:
                src = os.open(tmp, _READ_FLAGS, dir_fd=dir_fd)
                try:
                    while chunk := os.read(src, CHUNK):
                        _write_all(out, chunk)
                finally:
                    os.close(src)
                os.fsync(out)
            finally:
                os.close(out)
        except BaseException:
            _unlink_quietly(dir_fd, name)  # never leave a truncated file under the real name
            raise

    async def move(self, src: str, dst: str) -> None:
        def fn() -> None:
            src_rel, dst_rel = self._resolve(src), self._resolve(dst)
            try:
                src_fd = self._open_dir(src_rel.split("/")[:-1])
            except (FileNotFoundError, NotADirectoryError):
                raise NotFound(src_rel) from None
            try:
                src_name = src_rel.rpartition("/")[2]
                try:
                    mode = os.stat(src_name, dir_fd=src_fd, follow_symlinks=False).st_mode
                except (FileNotFoundError, NotADirectoryError):
                    raise NotFound(src_rel) from None
                if not stat.S_ISREG(mode):
                    raise NotFound(src_rel)
                self._require_marker()
                with self._parent(dst_rel, create=True) as (dst_fd, dst_name):
                    self._move_no_clobber(src_fd, src_name, dst_rel, dst_fd, dst_name)
            finally:
                os.close(src_fd)

        await self._run("move", fn, idempotent=False)

    def _move_no_clobber(
        self, src_fd: int, src_name: str, dst_rel: str, dst_fd: int, dst_name: str
    ) -> None:
        """link + unlink: the link fails if the destination exists. Without hard links the
        destination is checked just before a rename (a narrow race on such shares)."""
        try:
            os.link(src_name, dst_name, src_dir_fd=src_fd, dst_dir_fd=dst_fd, follow_symlinks=False)
        except FileExistsError:
            raise PreconditionFailed(self._stat_at(dst_rel, dst_fd, dst_name)) from None
        except OSError as exc:
            if exc.errno not in _NO_LINK:
                raise
            if _lexists(dst_fd, dst_name):
                raise PreconditionFailed(self._stat_at(dst_rel, dst_fd, dst_name)) from None
            os.rename(src_name, dst_name, src_dir_fd=src_fd, dst_dir_fd=dst_fd)
        else:
            os.unlink(src_name, dir_fd=src_fd)
        _fsync_dir(dst_fd)

    async def delete(self, path: str) -> None:
        def fn() -> None:
            rel = self._resolve(path)
            try:
                with self._parent(rel) as (dir_fd, name):
                    mode = os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode
                    if stat.S_ISREG(mode):
                        _unlink_quietly(dir_fd, name)
            except (FileNotFoundError, NotADirectoryError):
                return

        await self._run("delete", fn, idempotent=True)

    async def ensure_folder(self, path: str) -> None:
        """mkdir -p below the root, by folder fd like every write, and only while the
        marker is there (a bare mount point stays empty)."""

        def fn() -> None:
            rel = self._resolve(path)
            self._require_marker()
            os.close(self._open_dir(rel.split("/"), create=True))

        await self._run("ensure_folder", fn, idempotent=True)

    async def health(self) -> Health:
        present = await asyncio.to_thread(self._marker_present)
        return Health.ok() if present else Health.degraded("marker_missing")


def _write_all(fd: int, chunk: bytes) -> None:
    view = memoryview(chunk)
    while view:
        view = view[os.write(fd, view) :]


def _unlink_quietly(dir_fd: int, name: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(name, dir_fd=dir_fd)


def _lexists(dir_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _fsync_dir(dir_fd: int) -> None:
    """Make a new name durable; some shares cannot fsync a folder."""
    with contextlib.suppress(OSError):
        os.fsync(dir_fd)
