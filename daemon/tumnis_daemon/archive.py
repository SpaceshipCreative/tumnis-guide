"""Profile archive and restore (P2-18, FR-5.10).

`archive` packs a profile's home (`<hermes_profiles_dir>/<profile>`) into one tar file
streamed through zstd, `<state_dir>/archives/<archive_id>.tar.zst`, checks the pack against
the tree's manifest, removes the home and takes the profile out of the live list (the
archived names are kept in `<state_dir>/archived.json`). `restore` unpacks the pack into a
staging folder next to the profiles folder, checks its manifest digest against the one the
server expects, and only then moves it into place. `purge_archive` deletes the pack for
good. Each answer is the same when the server resends its command: a small record per
archive (`<state_dir>/archive-index/<archive_id>.json`) keeps the `archive_done` fields.

The manifest is `(relative posix path, size, sha256)` per regular file and per symlink
(a symlink's entry is its target text), sorted by path; its digest is the sha256 of its
canonical JSON, the same form as the server's `tumnis.core.archive_blobs.Manifest`.

The pack is written with python-zstandard's `ZstdCompressor.stream_writer` and read with
`ZstdDecompressor.stream_reader` (https://python-zstandard.readthedocs.io/), with tarfile
in stream mode (`w|`, `r|`). Its entries are built here (regular files, directories and
symlinks only; never hard links). Extraction uses a filter that runs `tarfile.tar_filter`
for its safety checks (no absolute names, nothing outside the destination, even through a
symlink) and then puts the member's permission bits back, since `tar_filter` clears group
and other write bits (https://docs.python.org/3.13/library/tarfile.html#extraction-filters).
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import tarfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import zstandard

from tumnis_daemon.config import DaemonConfig
from tumnis_daemon.protocol import (
    ERROR_MAX,
    NAME_RE,
    Archive,
    ArchiveDone,
    ArchiveErrorCode,
    PurgeArchive,
    Restore,
    RestoreDone,
    envelope,
)
from tumnis_daemon.provision import PROFILES_FILE, remembered_profiles

if TYPE_CHECKING:
    from tumnis_daemon.state import StateStore

__all__ = [
    "Manifest",
    "archive",
    "dispatch",
    "handle_archive",
    "handle_purge",
    "handle_restore",
    "live_profiles",
    "manifest_of",
    "restore",
]

log = logging.getLogger(__name__)

ARCHIVE_ID_RE: Final = r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$"
ARCHIVES_DIR: Final = "archives"
INDEX_DIR: Final = "archive-index"
ARCHIVED_FILE: Final = "archived.json"
LEVEL: Final = 10  # zstd level: written once, read rarely
PERMISSION_BITS: Final = 0o777  # never setuid, setgid or sticky
CHUNK: Final = 1 << 20

# One archive operation at a time: they share archived.json and profiles.json.
_LOCK: Final = asyncio.Lock()


@dataclass(frozen=True)
class Manifest:
    """(relative posix path, size, sha256 hex) per entry, sorted by path."""

    entries: tuple[tuple[str, int, str], ...]

    def canonical(self) -> bytes:
        return json.dumps(
            [[path, size, sha] for path, size, sha in self.entries],
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def digest(self) -> str:
        """sha256 of the canonical JSON: `[[path, size, sha256], ...]`, no spaces."""
        return hashlib.sha256(self.canonical()).hexdigest()

    @classmethod
    def of(cls, entries: list[tuple[str, int, str]]) -> "Manifest":
        return cls(tuple(sorted(entries)))


# --- the tree ------------------------------------------------------------------------------


def _walk(root: Path, prefix: str = "") -> Iterator[tuple[str, os.stat_result, Path]]:
    """(relative posix path, lstat, path) of everything under `root`, by name, parents
    before their children; links are never followed."""
    with os.scandir(root) as it:
        found = sorted(it, key=lambda e: e.name)
    for entry in found:
        rel = f"{prefix}{entry.name}"
        st = entry.stat(follow_symlinks=False)
        yield rel, st, Path(entry.path)
        if stat.S_ISDIR(st.st_mode):
            yield from _walk(Path(entry.path), f"{rel}/")


def _sha_file(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _link_entry(rel: str, target: str) -> tuple[str, int, str]:
    raw = os.fsencode(target)
    return rel, len(raw), hashlib.sha256(raw).hexdigest()


def manifest_of(root: Path) -> Manifest:
    """The manifest of every regular file and symlink under `root` (directories are not
    entries; a symlink's entry is its target text)."""
    entries: list[tuple[str, int, str]] = []
    for rel, st, path in _walk(root):
        if stat.S_ISLNK(st.st_mode):
            entries.append(_link_entry(rel, os.readlink(path)))
        elif stat.S_ISREG(st.st_mode):
            entries.append((rel, st.st_size, _sha_file(path)))
    return Manifest.of(entries)


# --- the pack ------------------------------------------------------------------------------


def _member(name: str, st: os.stat_result, path: Path) -> tarfile.TarInfo | None:
    """The tar entry of one directory, regular file or symlink; None for anything else."""
    info = tarfile.TarInfo(name)
    info.mode = stat.S_IMODE(st.st_mode) & PERMISSION_BITS
    info.mtime = int(st.st_mtime)
    if stat.S_ISDIR(st.st_mode):
        info.type = tarfile.DIRTYPE
    elif stat.S_ISLNK(st.st_mode):
        info.type = tarfile.SYMTYPE
        info.linkname = os.readlink(path)
    elif stat.S_ISREG(st.st_mode):
        info.type = tarfile.REGTYPE
        info.size = st.st_size
    else:
        return None
    return info


def _pack(root: Path, dest: Path) -> None:
    """Write `root` (the folder itself as `.`, then its tree) to `dest` as tar + zstd."""
    cctx = zstandard.ZstdCompressor(level=LEVEL)
    with dest.open("xb") as raw:
        with (
            cctx.stream_writer(raw, closefd=False) as zw,
            tarfile.open(fileobj=zw, mode="w|", format=tarfile.PAX_FORMAT) as tar,
        ):
            top = _member(".", root.lstat(), root)
            if top is not None:
                tar.addfile(top)
            for rel, st, path in _walk(root):
                info = _member(rel, st, path)
                if info is None:
                    log.info("archive_skipped_special_file")
                elif info.isreg():
                    with path.open("rb") as f:
                        tar.addfile(info, f)
                else:
                    tar.addfile(info)
        raw.flush()
        os.fsync(raw.fileno())


def _pack_manifest(pack: Path) -> Manifest:
    """The manifest of the files and symlinks inside a pack (read in one pass)."""
    entries: list[tuple[str, int, str]] = []
    with (
        pack.open("rb") as raw,
        zstandard.ZstdDecompressor().stream_reader(raw) as zr,
        tarfile.open(fileobj=zr, mode="r|") as tar,
    ):
        for member in tar:
            if member.issym():
                entries.append(_link_entry(member.name, member.linkname))
            elif member.isreg():
                f = tar.extractfile(member)
                if f is None:
                    raise ValueError("unreadable archive member")
                digest = hashlib.sha256()
                with f:
                    while chunk := f.read(CHUNK):
                        digest.update(chunk)
                entries.append((member.name, member.size, digest.hexdigest()))
    return Manifest.of(entries)


def _restore_filter(member: tarfile.TarInfo, dest: str) -> tarfile.TarInfo:
    """`tar_filter`'s safety checks, then the member's own permission bits back (without
    setuid, setgid or sticky). Only directories, regular files and symlinks are packed;
    anything else (a hard link, a device) is refused. Owners are never set: the daemon
    never runs as root, and tarfile changes owners only for root."""
    if not (member.isdir() or member.isreg() or member.issym()):
        raise tarfile.FilterError(f"refused archive member type for {member.name!r}")
    safe = tarfile.tar_filter(member, dest)
    return safe.replace(mode=member.mode & PERMISSION_BITS, deep=False)


def _extract(pack: Path, dest: Path) -> None:
    with (
        pack.open("rb") as raw,
        zstandard.ZstdDecompressor().stream_reader(raw) as zr,
        tarfile.open(fileobj=zr, mode="r|") as tar,
    ):
        tar.extractall(dest, filter=_restore_filter)  # noqa: S202  # filtered: _restore_filter


# --- the daemon's files --------------------------------------------------------------------


def _read_names(path: Path) -> list[str]:
    try:
        names = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    if not isinstance(names, list):
        return []
    return [n for n in names if isinstance(n, str) and re.fullmatch(NAME_RE, n)]


def _write_json(path: Path, data: Any) -> None:
    """Atomically: a temporary file beside it, then a rename."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def _archived(state_dir: Path) -> list[str]:
    return _read_names(state_dir / ARCHIVED_FILE)


def _set_archived(state_dir: Path, name: str, *, archived: bool) -> None:
    names = _archived(state_dir)
    if (name in names) == archived:
        return
    kept = sorted({*names, name}) if archived else [n for n in names if n != name]
    _write_json(state_dir / ARCHIVED_FILE, kept)


def _forget(state_dir: Path, name: str) -> None:
    """Drop a purged profile from the names this daemon created or linked."""
    names = remembered_profiles(state_dir)
    if name in names:
        _write_json(state_dir / PROFILES_FILE, [n for n in names if n != name])


def live_profiles(cfg: DaemonConfig) -> list[str]:
    """The profiles `register` lists: `daemon.toml`'s and the ones this daemon created or
    linked, in that order, without the archived ones."""
    archived = set(_archived(cfg.state_dir))
    names = dict.fromkeys([*cfg.profiles, *remembered_profiles(cfg.state_dir)])
    return [n for n in names if n not in archived]


def _home(cfg: DaemonConfig, profile: str) -> Path:
    return cfg.hermes_profiles_dir / profile


def _pack_path(cfg: DaemonConfig, archive_id: str) -> Path:
    return cfg.state_dir / ARCHIVES_DIR / f"{archive_id}.tar.zst"


def _index_path(cfg: DaemonConfig, archive_id: str) -> Path:
    return cfg.state_dir / INDEX_DIR / f"{archive_id}.json"


def _staging(cfg: DaemonConfig, archive_id: str) -> Path:
    """Beside the profiles folder (same filesystem, so the move into place is a rename),
    where Hermes never looks for a profile."""
    return cfg.hermes_profiles_dir.parent / f".tumnis-restore-{archive_id}"


def _is_home(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def _valid(profile: str, archive_id: str) -> bool:
    return bool(re.fullmatch(NAME_RE, profile) and re.fullmatch(ARCHIVE_ID_RE, archive_id))


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:ERROR_MAX]


# --- archive -------------------------------------------------------------------------------


def _archive_error(msg: Archive, code: ArchiveErrorCode, error: str) -> ArchiveDone:
    return ArchiveDone(
        **envelope(msg.correlation_id),
        archive_id=msg.archive_id,
        path="",
        size=0,
        sha256="",
        manifest_digest="",
        error_code=code,
        error=error,
    )


def _finish_archive(cfg: DaemonConfig, profile: str, home: Path) -> None:
    """Out of the live list first, then the home goes (a crash in between is finished by
    the resent `archive`)."""
    _set_archived(cfg.state_dir, profile, archived=True)
    if _is_home(home):
        shutil.rmtree(home)


def _archive_sync(msg: Archive, cfg: DaemonConfig, busy: frozenset[str]) -> ArchiveDone:
    home = _home(cfg, msg.profile)
    pack = _pack_path(cfg, msg.archive_id)
    index = _index_path(cfg, msg.archive_id)
    fields_keys = ("path", "size", "sha256", "manifest_digest")
    if index.is_file() and pack.is_file():  # a resent archive: the same answer
        kept = json.loads(index.read_text(encoding="utf-8"))
        _finish_archive(cfg, msg.profile, home)
        return ArchiveDone(
            **envelope(msg.correlation_id),
            archive_id=msg.archive_id,
            **{k: kept[k] for k in fields_keys},
        )
    if msg.profile in busy:
        return _archive_error(msg, "active_run", "a run of this profile is in progress")
    if not _is_home(home):
        return _archive_error(msg, "not_found", "no such profile home")
    before = manifest_of(home)
    pack.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = pack.with_name(f".{pack.name}.tmp")
    tmp.unlink(missing_ok=True)
    try:
        _pack(home, tmp)
        if _pack_manifest(tmp) != before:
            raise ValueError("the archive does not match the profile's files")
        tmp.replace(pack)
    finally:
        tmp.unlink(missing_ok=True)
    _fsync_dir(pack.parent)
    fields: dict[str, Any] = {
        "path": str(pack),
        "size": pack.stat().st_size,
        "sha256": _sha_file(pack),
        "manifest_digest": before.digest(),
    }
    _write_json(index, {"profile": msg.profile, **fields})
    _finish_archive(cfg, msg.profile, home)
    return ArchiveDone(**envelope(msg.correlation_id), archive_id=msg.archive_id, **fields)


async def handle_archive(
    msg: Archive, cfg: DaemonConfig, *, busy: frozenset[str] = frozenset()
) -> ArchiveDone:
    """The answer to one `archive`: the pack and the manifest digest, or an error code
    (`active_run` while a run of the profile is in progress; nothing is written then)."""
    if not _valid(msg.profile, msg.archive_id):
        return _archive_error(msg, "invalid_name", "invalid profile name or archive id")
    async with _LOCK:
        try:
            return await asyncio.to_thread(_archive_sync, msg, cfg, busy)
        except Exception as exc:  # any failure is an answer, never a silent wait
            log.warning(
                "archive_failed", extra={"profile": msg.profile, "kind": type(exc).__name__}
            )
            return _archive_error(msg, "failed", _error_text(exc))


# --- restore -------------------------------------------------------------------------------


def _restore_done(msg: Restore, digest: str, error: str | None = None) -> RestoreDone:
    return RestoreDone(
        **envelope(msg.correlation_id),
        archive_id=msg.archive_id,
        manifest_digest=digest,
        ok=error is None,
        error=error,
    )


def _finish_restore(cfg: DaemonConfig, msg: Restore) -> None:
    _set_archived(cfg.state_dir, msg.profile, archived=False)
    _pack_path(cfg, msg.archive_id).unlink(missing_ok=True)
    _index_path(cfg, msg.archive_id).unlink(missing_ok=True)


def _restore_sync(msg: Restore, cfg: DaemonConfig) -> RestoreDone:
    home = _home(cfg, msg.profile)
    pack = _pack_path(cfg, msg.archive_id)
    mismatch = "the restored files do not match the expected manifest"
    if home.exists() or home.is_symlink():
        # A resent restore (done already, or cut short before the pack went), or a home in
        # the way: ok only when the home is the archived tree.
        if not _is_home(home):
            return _restore_done(msg, "", "the profile home is not a folder")
        digest = manifest_of(home).digest()
        if digest != msg.expected_manifest_digest:
            return _restore_done(msg, digest, mismatch)
        _finish_restore(cfg, msg)
        return _restore_done(msg, digest)
    if not pack.is_file():
        return _restore_done(msg, "", "no such archive")
    staging = _staging(cfg, msg.archive_id)
    shutil.rmtree(staging, ignore_errors=True)
    try:
        staging.mkdir(mode=0o700, parents=True)
        _extract(pack, staging)
        digest = manifest_of(staging).digest()
        if digest != msg.expected_manifest_digest:
            return _restore_done(msg, digest, mismatch)
        home.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(home)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    _finish_restore(cfg, msg)
    return _restore_done(msg, digest)


async def handle_restore(msg: Restore, cfg: DaemonConfig) -> RestoreDone:
    """The answer to one `restore`: ok, with the restored tree's manifest digest, only when
    it is the expected one; otherwise the pack is kept and the profile stays archived."""
    if not _valid(msg.profile, msg.archive_id):
        return _restore_done(msg, "", "invalid profile name or archive id")
    async with _LOCK:
        try:
            return await asyncio.to_thread(_restore_sync, msg, cfg)
        except Exception as exc:  # any failure is an answer, never a silent wait
            log.warning(
                "restore_failed", extra={"profile": msg.profile, "kind": type(exc).__name__}
            )
            return _restore_done(msg, "", _error_text(exc))


# --- purge ---------------------------------------------------------------------------------


def _purge_sync(msg: PurgeArchive, cfg: DaemonConfig) -> None:
    _pack_path(cfg, msg.archive_id).unlink(missing_ok=True)
    _index_path(cfg, msg.archive_id).unlink(missing_ok=True)
    shutil.rmtree(_staging(cfg, msg.archive_id), ignore_errors=True)
    if msg.profile not in _archived(cfg.state_dir):
        return  # live again (restored meanwhile): its lists stay as they are
    _forget(cfg.state_dir, msg.profile)
    if msg.profile not in cfg.profiles:
        # Gone for good. A profile daemon.toml names stays hidden instead: dropping it
        # from archived.json would list a profile without a home.
        _set_archived(cfg.state_dir, msg.profile, archived=False)


async def handle_purge(msg: PurgeArchive, cfg: DaemonConfig) -> None:
    """Delete the archive for good (idempotent); the profile does not come back."""
    if not _valid(msg.profile, msg.archive_id):
        log.warning("purge_refused_invalid_name")
        return
    async with _LOCK:
        try:
            await asyncio.to_thread(_purge_sync, msg, cfg)
        except OSError as exc:
            log.warning("purge_failed", extra={"profile": msg.profile, "kind": type(exc).__name__})


# --- reliable answers ----------------------------------------------------------------------


async def archive(msg: Archive, state: "StateStore", cfg: DaemonConfig) -> None:
    """Answer an `archive` reliably (kept until the server acks it); a profile with a run
    in progress (running or waiting for its turn) is refused."""
    result = await handle_archive(msg, cfg, busy=state.busy_profiles())
    await state.send_reliably(result)


async def restore(msg: Restore, state: "StateStore", cfg: DaemonConfig) -> None:
    """Answer a `restore` reliably (kept until the server acks it)."""
    await state.send_reliably(await handle_restore(msg, cfg))


async def dispatch(
    msg: Archive | Restore | PurgeArchive, state: "StateStore", cfg: DaemonConfig
) -> None:
    """Act on one archive command; `purge_archive` has no answer beyond its ack."""
    match msg:
        case Archive():
            await archive(msg, state, cfg)
        case Restore():
            await restore(msg, state, cfg)
        case PurgeArchive():
            await handle_purge(msg, cfg)
