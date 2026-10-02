"""`SftpStorage`: a location on an SFTP server, over asyncssh (P3-14, FR-15.7, FR-15.12).

Connecting (asyncssh 2.24, `asyncssh.connect`):

- the host passes the SSRF guard (`resolve_and_check`) and the connection goes to the
  address the guard returned, never to a name resolved again;
- the server must present the pinned host key: `known_hosts` is the trusted-keys tuple
  `([pinned], [], [])`, so no known_hosts file, no CA and no ~/.ssh/config (`config=None`)
  is consulted, and a different key fails the handshake (`HostKeyChanged`);
- key authentication only: the location's private key, `preferred_auth=["publickey"]`,
  password, keyboard-interactive, host-based and GSS logins off, no ssh-agent. A server
  that accepts none of that fails with `AdapterRejected` and never sees a password.

Paths: every path passes `safe_rel_path`, then each component below the root is checked
with `lstat`: a symlink anywhere, even one pointing inside the root, is refused and never
followed (SFTP has no fd-relative calls, so a swap between the check and the operation is
a narrow race, as the plan accepts). Listings leave symlinks and temp files out.

Writes upload to `.tumnis-tmp-<uuid>` beside the target (created exclusively, 0600). A
create is a plain SFTP v3 `rename`, which OpenSSH's sftp-server performs as link + unlink
and so refuses an existing target. A replace hashes the target afresh and compares it with
`if_match` just before a `posix-rename@openssh.com`; a server without that extension
answers a conflict (never delete-then-rename). Etags are the sha256 of the content; hashes
are cached process-wide per (server, path, size, mtime), only for files whose mtime is
old enough that a same-second rewrite cannot hide behind it.
"""

# The names are the plan's shared contract (P3-14 interfaces), not "...Error".
# ruff: noqa: N818

import asyncio
import builtins
import contextlib
import hashlib
import posixpath
import stat
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, ClassVar, Final

import asyncssh
from pydantic import BaseModel

from tumnis.core.adapters.base import Adapter as AdapterBase
from tumnis.core.adapters.base import CallPolicy
from tumnis.core.adapters.errors import AdapterError, AdapterRejected, AdapterUnavailable
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.net import NetPolicy, Resolver, resolve_and_check, system_resolver
from tumnis.modules.knowledge.rules import etag_equal, safe_rel_path
from tumnis.modules.knowledge.storage import (
    LIST_PAGE_SIZE,
    MAX_FILE_BYTES,
    FileStat,
    Health,
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
SFTP_PORTS: Final = frozenset({22})
CONNECT_TIMEOUT_S: Final = 10.0
POLICY: Final = CallPolicy(timeout_s=120.0)  # a 50 MiB upload on a slow link; plan default
PROBE_KEY_ALGS: Final = (
    "ssh-ed25519",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "rsa-sha2-512",
    "rsa-sha2-256",
)
RACY_S: Final = 2.0  # a file changed this recently may change again within its mtime
_HASH_CACHE_MAX: Final = 200_000
_HASHES: "OrderedDict[tuple[Any, ...], str]" = OrderedDict()


class HostKeyChanged(StorageError):
    """The server presented a host key other than the pinned one; nothing was sent."""


class ProbedKey(BaseModel, frozen=True):
    openssh: str  # "ssh-ed25519 AAAA..." as known_hosts has it
    sha256: str  # "SHA256:..." as `ssh-keygen -lf` shows it


def sftp_policy(policy: NetPolicy, port: int) -> NetPolicy:
    """The ports an SFTP host may use: 22, and in self-hosted mode the port the location
    names (a NAS on its own port)."""
    ports = policy.ports | SFTP_PORTS
    if policy.mode == "self-hosted":
        ports |= {port}
    return replace(policy, ports=ports)


async def _address(host: str, port: int, net: NetPolicy | None, resolver: Resolver) -> str:
    """The address to connect to: the SSRF guard's answer, or `host` without a policy."""
    if net is None:
        return host
    return str(await resolve_and_check(host, port, sftp_policy(net, port), resolver))


def openssh_key(key: asyncssh.SSHKey) -> str:
    """'<type> <base64>' without a comment, as known_hosts has it."""
    key_type, data, *_ = key.export_public_key().decode().split()
    return f"{key_type} {data}"


async def probe_host_key(
    host: str, port: int, *, net_policy: NetPolicy, resolver: Resolver = system_resolver
) -> ProbedKey:
    """The host key the server presents (ed25519 preferred), through the SSRF guard; no
    login is attempted. Raises SsrfBlocked, or AdapterUnavailable when it does not answer."""
    address = await _address(host, port, net_policy, resolver)
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT_S):
            key = await asyncssh.get_server_host_key(
                address, port, server_host_key_algs=list(PROBE_KEY_ALGS), config=None
            )
    except (TimeoutError, OSError, asyncssh.Error) as exc:
        raise AdapterUnavailable(SftpStorage.name, "probe", type(exc).__name__) from exc
    if key is None:  # GSS key exchange only: nothing to pin
        raise AdapterRejected(SftpStorage.name, "probe", "no_host_key")
    return ProbedKey(openssh=openssh_key(key), sha256=str(key.get_fingerprint("sha256")))


def fingerprint(openssh: str) -> str:
    """'SHA256:…' of a public key in known_hosts form, as `ssh-keygen -lf` shows it."""
    return str(asyncssh.import_public_key(openssh).get_fingerprint("sha256"))


def check_private_key(pem: bytes) -> None:
    """ValueError unless `pem` is a private key asyncssh reads without a passphrase."""
    try:
        asyncssh.import_private_key(pem)
    except ValueError as exc:  # asyncssh.KeyImportError among them
        raise ValueError("not a readable private key without a passphrase") from exc


def _is_link(attrs: asyncssh.SFTPAttrs) -> bool:
    return attrs.permissions is not None and stat.S_ISLNK(attrs.permissions)


def _is_dir(attrs: asyncssh.SFTPAttrs) -> bool:
    return attrs.permissions is not None and stat.S_ISDIR(attrs.permissions)


def _is_file(attrs: asyncssh.SFTPAttrs) -> bool:
    return attrs.permissions is not None and stat.S_ISREG(attrs.permissions)


class SftpStorage(AdapterBase):
    name: ClassVar[str] = "knowledge.sftp"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        private_key_pem: bytes,
        pinned_host_key: str,
        root: str,
        net_policy: NetPolicy | None = None,
        resolver: Resolver = system_resolver,
        clock: Clock | None = None,
        policy: CallPolicy | None = None,
    ) -> None:
        super().__init__(policy=policy or POLICY, clock=clock or SystemClock())
        self.host, self.port, self.username = host, port, username
        self.root = sftp_root(root)
        self._key = private_key_pem
        self._pinned = pinned_host_key.strip()
        self._net = net_policy
        self._resolver = resolver
        self._conn: asyncssh.SSHClientConnection | None = None
        self._sftp: asyncssh.SFTPClient | None = None
        self._lock = asyncio.Lock()

    # --- Connection ----------------------------------------------------------------------

    async def _client(self) -> asyncssh.SFTPClient:
        async with self._lock:
            if self._sftp is None:
                address = await _address(self.host, self.port, self._net, self._resolver)
                trusted = asyncssh.import_public_key(self._pinned)
                conn = await asyncssh.connect(
                    address,
                    self.port,
                    username=self.username,
                    client_keys=[asyncssh.import_private_key(self._key)],
                    known_hosts=([trusted], [], []),
                    config=None,
                    preferred_auth=["publickey"],
                    public_key_auth=True,
                    password=None,
                    password_auth=False,
                    kbdint_auth=False,
                    host_based_auth=False,
                    gss_auth=False,
                    gss_kex=False,
                    agent_path=None,
                    agent_forwarding=False,
                    connect_timeout=CONNECT_TIMEOUT_S,
                )
                try:
                    self._sftp = await conn.start_sftp_client()
                except BaseException:
                    conn.close()
                    raise
                self._conn = conn
            return self._sftp

    async def _drop(self) -> None:
        async with self._lock:
            conn, self._conn, self._sftp = self._conn, None, None
        if conn is not None:
            conn.close()
            with contextlib.suppress(Exception):
                await conn.wait_closed()

    async def aclose(self) -> None:
        await self._drop()

    async def _call[T](self, op: str, fn: Callable[[], Awaitable[T]], *, idempotent: bool) -> T:
        """`fn` through the adapter's timeout and breaker, asyncssh errors translated; a
        broken connection is dropped so the next call reconnects."""

        async def attempt() -> T:
            try:
                return await fn()
            except (StorageError, AdapterError):
                raise
            except asyncssh.HostKeyNotVerifiable as exc:
                await self._drop()
                raise HostKeyChanged(f"{self.host}:{self.port}: {exc.reason}") from None
            except asyncssh.PermissionDenied as exc:
                await self._drop()
                raise AdapterRejected(self.name, op, "auth_failed") from exc
            except asyncssh.SFTPPermissionDenied as exc:
                raise AdapterRejected(self.name, op, "permission_denied") from exc
            except asyncssh.SFTPError as exc:
                raise AdapterRejected(self.name, op, type(exc).__name__) from exc
            except (asyncssh.Error, OSError) as exc:
                await self._drop()
                raise AdapterUnavailable(self.name, op, type(exc).__name__) from exc

        return await call_storage(self, op, attempt, idempotent=idempotent)

    # --- Paths ---------------------------------------------------------------------------

    def _full(self, rel: str) -> str:
        return f"{self.root}/{rel}" if rel else self.root

    async def _lstat(self, sftp: asyncssh.SFTPClient, full: str) -> asyncssh.SFTPAttrs | None:
        try:
            return await sftp.lstat(full)
        except asyncssh.SFTPNoSuchFile:
            return None

    async def _walk_parts(self, sftp: asyncssh.SFTPClient, rel: str) -> asyncssh.SFTPAttrs | None:
        """lstat each component of `rel` below the root: a symlink anywhere ->
        PathRejected, a file where a folder should be -> PathRejected; the last
        component's attributes, or None when something on the way is missing."""
        parts = rel.split("/")
        attrs: asyncssh.SFTPAttrs | None = None
        for n in range(1, len(parts) + 1):
            attrs = await self._lstat(sftp, self._full("/".join(parts[:n])))
            if attrs is None:
                return None
            if _is_link(attrs):
                raise PathRejected(f"{rel!r}: a symlink is on the way")
            if n < len(parts) and not _is_dir(attrs):
                raise PathRejected(f"{rel!r}: {parts[n - 1]!r} is not a folder")
        return attrs

    # --- Hashes --------------------------------------------------------------------------

    def _cache_key(self, rel: str, attrs: asyncssh.SFTPAttrs) -> tuple[Any, ...]:
        return (self.host, self.port, self.username, self._full(rel), attrs.size, attrs.mtime)

    async def _hash(self, sftp: asyncssh.SFTPClient, rel: str) -> str:
        digest = hashlib.sha256()
        async with sftp.open(self._full(rel), "rb") as f:
            while chunk := await f.read(CHUNK):
                digest.update(chunk if isinstance(chunk, bytes) else chunk.encode())
        return digest.hexdigest()

    async def _file_stat(
        self, sftp: asyncssh.SFTPClient, rel: str, attrs: asyncssh.SFTPAttrs, *, fresh: bool
    ) -> FileStat:
        key = self._cache_key(rel, attrs)
        etag = None if fresh else _HASHES.get(key)
        if etag is None:
            etag = await self._hash(sftp, rel)
            _remember(key, etag, attrs.mtime)
        else:
            _HASHES.move_to_end(key)
        return _stat(rel, attrs, etag)

    async def _stat_rel(self, rel: str, *, fresh: bool = False) -> FileStat | None:
        sftp = await self._client()
        attrs = await self._walk_parts(sftp, rel)
        if attrs is None or not _is_file(attrs):
            if attrs is not None and not _is_dir(attrs):
                raise PathRejected(f"{rel!r}: not a regular file")
            return None
        return await self._file_stat(sftp, rel, attrs, fresh=fresh)

    # --- The port ------------------------------------------------------------------------

    async def stat(self, path: str) -> FileStat | None:
        rel = safe_rel_path(path)
        return await self._call("stat", lambda: self._stat_rel(rel), idempotent=True)

    async def list(self, prefix: str, cursor: str | None) -> Page[FileStat]:
        safe = safe_prefix(prefix)

        async def fn() -> Page[FileStat]:
            sftp = await self._client()
            folder = safe[:-1] if safe.endswith("/") else safe.rpartition("/")[0]
            if folder:
                attrs = await self._walk_parts(sftp, folder)
                if attrs is None or not _is_dir(attrs):
                    return Page[FileStat](items=[], next_cursor=None)
            found = await self._walk(sftp, folder)
            names = sorted(
                rel for rel in found if rel.startswith(safe) and (cursor is None or rel > cursor)
            )
            items = [
                await self._file_stat(sftp, rel, found[rel], fresh=False)
                for rel in names[:LIST_PAGE_SIZE]
            ]
            more = len(names) > LIST_PAGE_SIZE
            return Page[FileStat](
                items=items, next_cursor=names[LIST_PAGE_SIZE - 1] if more else None
            )

        return await self._call("list", fn, idempotent=True)

    async def _walk(self, sftp: asyncssh.SFTPClient, folder: str) -> dict[str, asyncssh.SFTPAttrs]:
        """Regular files under root/`folder` with their attributes (readdir's are lstat's):
        symlinks are neither listed nor followed; temp files and unsafe names left out."""
        found: dict[str, asyncssh.SFTPAttrs] = {}
        pending = [folder]
        while pending:
            here = pending.pop()
            try:
                entries = await sftp.readdir(self._full(here))
            except asyncssh.SFTPNoSuchFile:
                continue
            for entry in entries:
                name = entry.filename
                if not isinstance(name, str) or name in {".", ".."}:
                    continue
                rel = posixpath.join(here, name) if here else name
                try:
                    rel = safe_rel_path(rel)
                except PathRejected:
                    continue  # a name Tumnis cannot address; the sync engine reports it
                if _is_dir(entry.attrs):
                    pending.append(rel)
                elif _is_file(entry.attrs) and not name.startswith(TMP_PREFIX):
                    found[rel] = entry.attrs
        return found

    async def read(self, path: str) -> AsyncIterator[bytes]:
        rel = safe_rel_path(path)

        async def open_file() -> Any:
            sftp = await self._client()
            attrs = await self._walk_parts(sftp, rel)
            if attrs is None or not _is_file(attrs):
                raise NotFound(rel)
            return await sftp.open(self._full(rel), "rb")

        f = await self._call("read", open_file, idempotent=True)
        try:
            while True:
                try:
                    chunk = await f.read(CHUNK)
                except (asyncssh.Error, OSError) as exc:
                    raise AdapterUnavailable(self.name, "read", type(exc).__name__) from exc
                if not chunk:
                    break
                yield chunk
        finally:
            with contextlib.suppress(Exception):
                await f.close()

    async def write(self, path: str, data: AsyncIterator[bytes], if_match: str | None) -> FileStat:
        rel = safe_rel_path(path)

        async def fn() -> FileStat:
            sftp = await self._client()
            target = await self._walk_parts(sftp, rel)
            if target is not None and not _is_file(target):
                raise PathRejected(f"{rel!r}: not a regular file")
            folder = rel.rpartition("/")[0]
            if folder:
                await self._make_folders(sftp, folder)
            tmp = self._full(f"{folder}/{TMP_PREFIX}{uuid.uuid4().hex}".lstrip("/"))
            try:
                size, etag = await self._upload(sftp, tmp, data)
                await self._commit(sftp, rel, tmp, if_match)
            finally:
                with contextlib.suppress(asyncssh.Error, OSError):
                    await sftp.remove(tmp)
            attrs = await sftp.lstat(self._full(rel))
            _remember(self._cache_key(rel, attrs), etag, attrs.mtime)
            return FileStat(path=rel, size=size, mtime=_mtime(attrs), etag=etag)

        return await self._call("write", fn, idempotent=False)

    async def _upload(
        self, sftp: asyncssh.SFTPClient, tmp: str, data: AsyncIterator[bytes]
    ) -> tuple[int, str]:
        digest, size = hashlib.sha256(), 0
        attrs = asyncssh.SFTPAttrs(permissions=0o600)
        async with sftp.open(tmp, "xb", attrs) as f:
            async for chunk in data:
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise TooLarge(f"over {MAX_FILE_BYTES} bytes")
                digest.update(chunk)
                await f.write(chunk)
        return size, digest.hexdigest()

    async def _commit(
        self, sftp: asyncssh.SFTPClient, rel: str, tmp: str, if_match: str | None
    ) -> None:
        full = self._full(rel)
        if if_match is None:
            try:
                await sftp.rename(tmp, full)  # v3: refuses an existing target (link + unlink)
            except asyncssh.SFTPError:
                current = await self._stat_rel(rel)
                if current is not None:
                    raise PreconditionFailed(current) from None
                raise
            return
        current = await self._stat_rel(rel, fresh=True)  # hashed just before
        if current is None or not etag_equal(current.etag, if_match):
            raise PreconditionFailed(current)
        try:
            await sftp.posix_rename(tmp, full)
        except asyncssh.SFTPOpUnsupported:
            raise PreconditionFailed(current) from None  # no atomic replace: a conflict

    async def _make_folders(self, sftp: asyncssh.SFTPClient, folder: str) -> None:
        """mkdir -p below the root, each component lstat'd first (a symlink or a file on the
        way is refused)."""
        parts = folder.split("/")
        for n in range(1, len(parts) + 1):
            here = "/".join(parts[:n])
            attrs = await self._lstat(sftp, self._full(here))
            if attrs is None:
                try:
                    await sftp.mkdir(self._full(here), asyncssh.SFTPAttrs(permissions=0o750))
                except asyncssh.SFTPFailure:
                    attrs = await self._lstat(sftp, self._full(here))  # made meanwhile
                    if attrs is None or not _is_dir(attrs):
                        raise
                continue
            if _is_link(attrs):
                raise PathRejected(f"{folder!r}: a symlink is on the way")
            if not _is_dir(attrs):
                raise PathRejected(f"{here!r} is not a folder")

    async def move(self, src: str, dst: str) -> None:
        src_rel, dst_rel = safe_rel_path(src), safe_rel_path(dst)

        async def fn() -> None:
            sftp = await self._client()
            attrs = await self._walk_parts(sftp, src_rel)
            if attrs is None or not _is_file(attrs):
                raise NotFound(src_rel)
            existing = await self._walk_parts(sftp, dst_rel)
            if existing is not None:
                current = await self._stat_rel(dst_rel) if _is_file(existing) else None
                raise PreconditionFailed(current)
            folder = dst_rel.rpartition("/")[0]
            if folder:
                await self._make_folders(sftp, folder)
            try:
                await sftp.rename(self._full(src_rel), self._full(dst_rel))  # no clobber
            except asyncssh.SFTPError:
                current = await self._stat_rel(dst_rel)
                if current is not None:
                    raise PreconditionFailed(current) from None
                raise

        await self._call("move", fn, idempotent=False)

    async def delete(self, path: str) -> None:
        rel = safe_rel_path(path)

        async def fn() -> None:
            sftp = await self._client()
            attrs = await self._walk_parts(sftp, rel)
            if attrs is None or not _is_file(attrs):
                return
            with contextlib.suppress(asyncssh.SFTPNoSuchFile):
                await sftp.remove(self._full(rel))

        await self._call("delete", fn, idempotent=True)

    async def ensure_folder(self, path: str) -> None:
        rel = safe_rel_path(path)

        async def fn() -> None:
            await self._make_folders(await self._client(), rel)

        await self._call("ensure_folder", fn, idempotent=True)

    async def health(self) -> Health:
        """The root answers as a folder (a chrooted user's scope confirmed). A changed host
        key is `host_key_changed`, a refused login `auth_failed`."""

        async def fn() -> Health:
            sftp = await self._client()
            attrs = await self._lstat(sftp, self.root)
            if attrs is None:
                return Health.degraded("root_missing")
            if not _is_dir(attrs):
                return Health.degraded("root_not_a_folder")
            return Health.ok()

        try:
            return await self._call("health", fn, idempotent=True)
        except HostKeyChanged:
            return Health.degraded("host_key_changed")
        except AdapterRejected as exc:
            return Health.degraded(getattr(exc, "code", None) or "rejected")
        except AdapterError:
            return Health.degraded("unreachable")


def sftp_root(root: str) -> str:
    """The location's folder on the server: '/'-separated, no '.' or '..' segments, no
    trailing '/'; relative to the login folder unless it starts with '/'."""
    stripped = root.strip()
    parts = [p for p in stripped.split("/") if p]
    if not parts or any(p in {".", ".."} for p in parts):
        raise PathRejected(f"{root!r}: not a usable SFTP folder")
    for part in parts:
        safe_rel_path(part)
    joined = "/".join(parts)
    return "/" + joined if stripped.startswith("/") else joined


def _mtime(attrs: asyncssh.SFTPAttrs) -> datetime:
    return datetime.fromtimestamp(attrs.mtime or 0, UTC)


def _stat(rel: str, attrs: asyncssh.SFTPAttrs, etag: str) -> FileStat:
    return FileStat(path=rel, size=attrs.size or 0, mtime=_mtime(attrs), etag=etag)


def _remember(key: tuple[Any, ...], etag: str, mtime: int | None) -> None:
    """Cache a hash only for a file whose mtime is safely in the past: SFTP v3 mtimes are
    whole seconds, so a rewrite in the same second with the same size would look the same."""
    if mtime is None or time.time() - mtime < RACY_S:
        return
    _HASHES[key] = etag
    _HASHES.move_to_end(key)
    while len(_HASHES) > _HASH_CACHE_MAX:
        _HASHES.popitem(last=False)


__all__: builtins.list[str] = [
    "HostKeyChanged",
    "ProbedKey",
    "SftpStorage",
    "check_private_key",
    "fingerprint",
    "openssh_key",
    "probe_host_key",
    "sftp_policy",
    "sftp_root",
]
