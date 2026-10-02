"""`FolderReader`: an Obsidian vault mounted read-only into the worker (P3-12, FR-15.10).

The vault is plain files; no Obsidian process is needed. The walk goes by folder fd
(`os.fwalk`, symlinks neither followed nor listed) and prunes what `skip` excludes before
descending, so an excluded folder's files are never opened. Files are opened with
O_NOFOLLOW relative to their folder's fd and hashed (sha256, cached per inode, size and
mtime so an unchanged file is not read again). A file whose size changes while it is read
(Syncthing writing it, plan risk) is left out of this listing; the next scan takes it.
Nothing here writes: the mount is read-only, and the reader would not write anyway.

`VaultFiles` is the filesystem part, shared with `GitReader` (which reads its clone).
"""

import asyncio
import errno
import hashlib
import os
import posixpath
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Final

from tumnis.core.adapters.base import Adapter, AdapterRejected, AdapterUnavailable, CallPolicy
from tumnis.core.adapters.retry import RetryPolicy
from tumnis.core.clock import Clock, SystemClock
from tumnis.modules.knowledge.adapters.obsidian.port import FileStat, Skip, never_skip
from tumnis.modules.knowledge.rules import PathRejected, safe_rel_path
from tumnis.modules.knowledge.storage import MAX_FILE_BYTES, NotFound, StorageError, TooLarge

__all__ = ["POLICY", "FolderReader", "VaultFiles"]

CHUNK: Final = 64 * 1024
# A big vault on a slow share still lists; the sync runs in a DBOS step, which retries.
POLICY: Final = CallPolicy(timeout_s=600.0, retry=RetryPolicy(max_attempts=1))
_READ_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_DIR_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
ALWAYS_PRUNED: Final = frozenset({".git"})


class VaultFiles:
    """Read-only access to the files under `root`, by fd."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        # (dev, inode, size, mtime_ns) -> sha256 hex
        self._hashes: dict[tuple[int, int, int, int], str] = {}

    def list_files(self, skip: Skip = never_skip) -> list[FileStat]:
        root_fd = os.open(self.root, _DIR_FLAGS)
        found: list[FileStat] = []
        try:
            walk = os.fwalk(".", dir_fd=root_fd, follow_symlinks=False, topdown=True)
            for here, dirs, files, here_fd in walk:
                base = posixpath.normpath(here)
                base = "" if base == "." else base
                dirs[:] = sorted(
                    d for d in dirs if d not in ALWAYS_PRUNED and not skip(_join(base, d))
                )
                for name in sorted(files):
                    rel = _join(base, name)
                    if skip(rel):
                        continue
                    item = self._stat(rel, here_fd, name)
                    if item is not None:
                        found.append(item)
        finally:
            os.close(root_fd)
        return sorted(found, key=lambda item: item.path)

    def _stat(self, rel: str, dir_fd: int, name: str) -> FileStat | None:
        try:
            safe = safe_rel_path(rel)
            fd = os.open(name, _READ_FLAGS, dir_fd=dir_fd)
        except (PathRejected, FileNotFoundError, OSError):
            return None  # a name Tumnis cannot address, gone, or a symlink
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                return None
            key = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            digest = self._hashes.get(key)
            if digest is None:
                digest = _hash(fd)
                after = os.fstat(fd)
                if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                    return None  # still being written
                self._hashes[key] = digest
        finally:
            os.close(fd)
        mtime = datetime.fromtimestamp(before.st_mtime, UTC)
        return FileStat(path=safe, size=before.st_size, mtime=mtime, etag=digest)

    def read(self, path: str) -> bytes:
        rel = safe_rel_path(path)
        parts = rel.split("/")
        if parts[0] in ALWAYS_PRUNED:
            raise PathRejected(f"{rel!r}: not vault content")
        fd = self._open(rel, parts)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise NotFound(rel)
            if info.st_size > MAX_FILE_BYTES:
                raise TooLarge(f"{rel!r} is over {MAX_FILE_BYTES} bytes")
            chunks: list[bytes] = []
            size = 0
            while chunk := os.read(fd, CHUNK):
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise TooLarge(f"{rel!r} is over {MAX_FILE_BYTES} bytes")
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(fd)

    def _open(self, rel: str, parts: list[str]) -> int:
        """The file's fd, walking folder by folder with O_NOFOLLOW: a missing part is
        NotFound, a symlink anywhere PathRejected."""
        dir_fd = os.open(self.root, _DIR_FLAGS)
        try:
            for part in parts[:-1]:
                next_fd = _open_at(rel, part, _DIR_FLAGS, dir_fd)
                os.close(dir_fd)
                dir_fd = next_fd
            return _open_at(rel, parts[-1], _READ_FLAGS, dir_fd)
        finally:
            os.close(dir_fd)


def _open_at(rel: str, name: str, flags: int, dir_fd: int) -> int:
    try:
        return os.open(name, flags, dir_fd=dir_fd)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise NotFound(rel) from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:  # O_NOFOLLOW met a symlink
            raise PathRejected(f"{rel!r}: a symlink is not vault content") from exc
        raise


def _join(base: str, name: str) -> str:
    return f"{base}/{name}" if base else name


def _hash(fd: int) -> str:
    digest = hashlib.sha256()
    while chunk := os.read(fd, CHUNK):
        digest.update(chunk)
    return digest.hexdigest()


async def run_files[T](adapter: Adapter, op: str, fn: Callable[[], T]) -> T:
    """`fn` in a worker thread through the adapter's timeout and breaker. The vault's own
    answers (a missing file, a refused path, too large) pass through untouched; a missing
    or unreadable vault root is an outage."""

    async def attempt() -> tuple[T | None, StorageError | None]:
        try:
            return await asyncio.to_thread(fn), None
        except StorageError as exc:
            return None, exc
        except PermissionError as exc:
            raise AdapterRejected(adapter.name, op, str(exc)) from exc
        except OSError as exc:
            raise AdapterUnavailable(adapter.name, op, str(exc)) from exc

    result, error = await adapter.call(op, attempt, idempotent=True)
    if error is not None:
        raise error
    return result  # type: ignore[return-value]  # set whenever error is None


class FolderReader(Adapter):
    name: ClassVar[str] = "knowledge.obsidian_folder"

    def __init__(
        self, root: Path | str, *, clock: Clock | None = None, policy: CallPolicy | None = None
    ) -> None:
        super().__init__(policy=policy or POLICY, clock=clock or SystemClock())
        self.files = VaultFiles(root)

    async def list_files(self, skip: Skip = never_skip) -> list[FileStat]:
        return await run_files(self, "list", lambda: self.files.list_files(skip))

    async def read(self, path: str) -> bytes:
        return await run_files(self, "read", lambda: self.files.read(path))

    async def refresh(self) -> None:
        """A mounted folder is always current."""
