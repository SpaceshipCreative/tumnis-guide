"""`GitReader`: an Obsidian vault pulled from a Git remote with a read-only deploy key
(P3-12, FR-15.10, Data flow rule 1).

The clone lives in `<data_dir>/<connection_id>/`, owned by the worker; the directory comes
from the connection id only, never from user input. Commands (git docs:
https://git-scm.com/docs/git, https://git-scm.com/docs/git-push):

- connect: `git clone --depth 1 --branch <branch> --single-branch --no-tags -- <remote>
  <dir>`, then the write probe `git push --dry-run origin
  HEAD:refs/heads/tumnis-write-probe`. The probe must fail: if it succeeds the key can
  write, the clone is removed and the connection refused (`WritableDeployKey`,
  `writable_deploy_key`). It fails closed: only a refusal for want of write access proves
  the key read-only; a host key mismatch, the network or DNS, or an answer Tumnis cannot
  read also remove the clone and refuse the connection (`write_probe_inconclusive`).
  GitHub deploy keys are read-only unless "Allow write access" is ticked (https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys);
  the probe proves it for any host. A `file://` remote has no key and no probe.
- refresh: `git fetch --no-tags --depth 1 origin <branch>`, then
  `git reset --hard FETCH_HEAD`.

Nothing else is ever run: no push without `--dry-run`, no hooks, no submodules.

Every command runs with a clean environment: no prompts (`GIT_TERMINAL_PROMPT=0`), no
system or global git config, only the remote's protocol allowed (`GIT_ALLOW_PROTOCOL`,
`ssh` or `file`; `ext` never), and for SSH a `GIT_SSH_COMMAND` with the deploy key
(`-i`, IdentitiesOnly), strict host key checking against the pinned known_hosts, batch
mode and no user ssh config. The key is written to a 0600 file for the one command and
removed after it; it is never put in the environment.

SSRF (AGENTS.md: non-HTTP clients connect to the address `resolve_and_check` returns):
before each network command the SSH host is resolved and checked against the net policy,
and ssh connects to that address (`HostName=<ip>`) while checking the host key under the
name (`HostKeyAlias=<host>`), so a DNS answer that changes after the check is not used. A
`file://` remote (a path on the worker) is refused in hosted mode.
"""

import asyncio
import contextlib
import dataclasses
import os
import re
import shlex
import shutil
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Final, Literal, Protocol
from urllib.parse import urlsplit
from uuid import UUID

from tumnis.core.adapters.base import Adapter, AdapterRejected, AdapterUnavailable, CallPolicy
from tumnis.core.adapters.retry import RetryPolicy
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.net import NetPolicy, Resolver, SsrfBlocked, resolve_and_check, system_resolver
from tumnis.modules.knowledge.adapters.obsidian.folder import VaultFiles, run_files
from tumnis.modules.knowledge.adapters.obsidian.port import FileStat, Skip, never_skip

__all__ = [
    "PROBE_REF",
    "CommandResult",
    "GitReader",
    "GitRunner",
    "HostKeyChanged",
    "InvalidRemote",
    "SubprocessGitRunner",
    "WritableDeployKey",
    "WriteProbeInconclusive",
]

PROBE_REF: Final = "HEAD:refs/heads/tumnis-write-probe"
SSH_PORT: Final = 22
SSH_PORTS: Final = frozenset({SSH_PORT, 2222})  # plan default: the usual SSH ports for Git hosts
COMMAND_TIMEOUT_S: Final = 300.0  # a first clone of a large vault (plan default)
POLICY: Final = CallPolicy(timeout_s=COMMAND_TIMEOUT_S + 30, retry=RetryPolicy(max_attempts=1))
GIT_OPTIONS: Final = (
    "-c",
    "protocol.ext.allow=never",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "submodule.recurse=false",
)
_BRANCH: Final = re.compile(r"^(?!-)(?!.*\.\.)(?!.*//)[A-Za-z0-9._/-]{1,200}(?<![./])$")
_SCP: Final = re.compile(r"^(?P<user>[A-Za-z0-9._-]+)@(?P<host>[A-Za-z0-9.-]+):(?P<path>[^\s]+)$")
_AUTH_FAILURES: Final = (
    "permission denied",
    "could not read from remote repository",
    "repository not found",
    "does not appear to be a git repository",
)
# ssh's own errors when it never reached the server: the probe proved nothing; try again.
_NETWORK_FAILURES: Final = (
    "could not resolve hostname",
    "connection timed out",
    "connection refused",
    "network is unreachable",
    "no route to host",
    "connection reset",
    "connection closed",
)
# How Git hosts word a push refused for want of write access (GitHub "marked as read
# only", GitLab "not allowed to push", Gitea/Forgejo "permission denied for writing",
# Bitbucket "deployment key is read-only", a plain server "insufficient permission").
_WRITE_REFUSALS: Final = (
    "read only",
    "read-only",
    "denied",
    "not allowed",
    "insufficient permission",
    "no write access",
    "not authorized",
)


class InvalidRemote(AdapterRejected):
    """The remote, branch or credentials cannot be used; nothing was run."""

    code = "invalid_remote"

    def __init__(self, reason: str) -> None:
        super().__init__(GitReader.name, "connect", reason)


class WritableDeployKey(AdapterRejected):
    """The deploy key can push: the connection is refused (the vault is read-only)."""

    code = "writable_deploy_key"

    def __init__(self) -> None:
        super().__init__(GitReader.name, "connect", "the deploy key can write to the repository")


class WriteProbeInconclusive(AdapterRejected):
    """The dry-run push failed without saying the key may not write: nothing proves the
    key read-only, so the connection is refused (the probe fails closed)."""

    code = "write_probe_inconclusive"

    def __init__(self, detail: str) -> None:
        super().__init__(
            GitReader.name, "connect", f"could not prove the deploy key read-only: {detail}"
        )


class HostKeyChanged(AdapterRejected):
    """The server did not show the pinned host key (Scott decision 82): the sync is
    refused and nothing is accepted automatically; the person re-confirms the key."""

    code = "host_key_changed"

    def __init__(self, op: str) -> None:
        super().__init__(
            GitReader.name, op, "host key changed: the server's key is not the pinned one"
        )


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class GitRunner(Protocol):
    async def __call__(
        self, argv: Sequence[str], *, env: Mapping[str, str], cwd: str | None, timeout_s: float
    ) -> CommandResult: ...


class SubprocessGitRunner:
    """Runs git as a child process (never through a shell); killed at its timeout."""

    async def __call__(
        self, argv: Sequence[str], *, env: Mapping[str, str], cwd: str | None, timeout_s: float
    ) -> CommandResult:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            env=dict(env),
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        return CommandResult(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=out.decode(errors="replace"),
            stderr=err.decode(errors="replace"),
        )


@dataclass(frozen=True)
class Remote:
    kind: Literal["ssh", "file"]
    url: str
    host: str | None = None
    port: int = SSH_PORT


def parse_remote(url: str, net: NetPolicy) -> Remote:
    """The remote as git will be given it: `ssh://[user@]host[:port]/path`, the scp form
    `user@host:path`, or (self-hosted only) `file:///path`."""
    url = url.strip()
    if not url or url.startswith("-") or any(c.isspace() for c in url):
        raise InvalidRemote("the remote is not a Git URL")
    scp = _SCP.match(url)
    if scp and "://" not in url:
        return Remote(kind="ssh", url=url, host=scp["host"], port=SSH_PORT)
    parts = urlsplit(url)
    if parts.scheme == "ssh" and parts.hostname:
        try:
            port = parts.port or SSH_PORT
        except ValueError as exc:
            raise InvalidRemote("the remote's port is not a number") from exc
        return Remote(kind="ssh", url=url, host=parts.hostname, port=port)
    if parts.scheme == "file" and parts.path.startswith("/") and not parts.netloc:
        if net.mode == "hosted":
            raise SsrfBlocked("file", "a path on the server is not allowed in hosted mode")
        return Remote(kind="file", url=url)
    raise InvalidRemote("use an ssh:// or user@host:path remote")


class GitReader(Adapter):
    name: ClassVar[str] = "knowledge.obsidian_git"

    def __init__(  # one keyword per setting of the connection
        self,
        *,
        connection_id: UUID,
        remote: str,
        branch: str,
        data_dir: Path | str,
        deploy_key: bytes | None,
        known_hosts: str | None,
        runner: GitRunner | None = None,
        net: NetPolicy,
        resolver: Resolver = system_resolver,
        clock: Clock | None = None,
        policy: CallPolicy | None = None,
    ) -> None:
        super().__init__(policy=policy or POLICY, clock=clock or SystemClock())
        self.remote = parse_remote(remote, net)
        if not _BRANCH.match(branch):
            raise InvalidRemote("the branch name is not valid")
        if self.remote.kind == "ssh" and not (deploy_key and known_hosts):
            raise InvalidRemote("an SSH remote needs a deploy key and its host key")
        self.branch = branch
        self.data_dir = Path(data_dir)
        self.dir = self.data_dir / str(connection_id)
        self._key = deploy_key
        self._known_hosts = known_hosts
        self._runner: GitRunner = runner or SubprocessGitRunner()
        self._net = dataclasses.replace(net, ports=net.ports | SSH_PORTS)
        self._resolver = resolver
        self.files = VaultFiles(self.dir)

    # --- The port ------------------------------------------------------------------------

    async def connect(self) -> None:
        """A fresh shallow clone, then the write probe (SSH remotes)."""
        await asyncio.to_thread(shutil.rmtree, self.dir, True)
        await self._clone()
        if self.remote.kind != "ssh":
            return
        probe = await self._git(
            "push", "--dry-run", "origin", PROBE_REF, network=True, in_clone=True
        )
        refusal = _probe_refusal(probe)
        if refusal is not None:
            await asyncio.to_thread(shutil.rmtree, self.dir, True)
            raise refusal

    async def refresh(self) -> None:
        if not await asyncio.to_thread((self.dir / ".git").is_dir):
            await self._clone()
        fetched = await self._git(
            "fetch", "--no-tags", "--depth", "1", "origin", self.branch, network=True, in_clone=True
        )
        self._check(fetched, "fetch")
        reset = await self._git("reset", "--hard", "FETCH_HEAD", network=False, in_clone=True)
        self._check(reset, "reset")

    async def list_files(self, skip: Skip = never_skip) -> list[FileStat]:
        return await run_files(self, "list", lambda: self.files.list_files(skip))

    async def read(self, path: str) -> bytes:
        return await run_files(self, "read", lambda: self.files.read(path))

    # --- Git -----------------------------------------------------------------------------

    async def _clone(self) -> None:
        await asyncio.to_thread(self.data_dir.mkdir, parents=True, exist_ok=True)
        cloned = await self._git(
            "clone",
            "--depth",
            "1",
            "--branch",
            self.branch,
            "--single-branch",
            "--no-tags",
            "--",
            self.remote.url,
            str(self.dir),
            network=True,
            in_clone=False,
        )
        self._check(cloned, "clone")

    def _check(self, result: CommandResult, op: str) -> None:
        if result.returncode == 0:
            return
        detail = result.stderr.strip().splitlines()[-1:] or [f"exit {result.returncode}"]
        if "host key verification failed" in result.stderr.lower():
            raise HostKeyChanged(op)
        if any(failure in result.stderr.lower() for failure in _AUTH_FAILURES):
            raise AdapterRejected(self.name, op, detail[0])
        raise AdapterUnavailable(self.name, op, detail[0])

    async def _git(self, *args: str, network: bool, in_clone: bool) -> CommandResult:
        argv = ["git", *GIT_OPTIONS]
        if in_clone:
            argv += ["-C", str(self.dir)]
        argv += list(args)
        address = None
        if network and self.remote.kind == "ssh" and self.remote.host is not None:
            ip = await resolve_and_check(
                self.remote.host, self.remote.port, self._net, self._resolver
            )
            address = str(ip)

        async def run() -> CommandResult:
            with self._ssh_files() as (key_path, hosts_path):
                env = self._env(key_path, hosts_path, address)
                try:
                    return await self._runner(argv, env=env, cwd=None, timeout_s=COMMAND_TIMEOUT_S)
                except OSError as exc:
                    raise AdapterUnavailable(self.name, args[0], str(exc)) from exc

        return await self.call(args[0], run, idempotent=args[0] != "push")

    def _env(
        self, key_path: Path | None, hosts_path: Path | None, address: str | None
    ) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.data_dir),
            "LANG": "C",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ALLOW_PROTOCOL": self.remote.kind,
        }
        if key_path is not None and hosts_path is not None:
            options = [
                "ssh",
                "-F",
                os.devnull,
                "-i",
                str(key_path),
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                f"UserKnownHostsFile={hosts_path}",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=20",
            ]
            if address is not None and self.remote.host is not None:
                options += ["-o", f"HostKeyAlias={self._alias()}", "-o", f"HostName={address}"]
            env["GIT_SSH_COMMAND"] = " ".join(shlex.quote(part) for part in options)
        return env

    def _alias(self) -> str:
        """The name the host key is looked up under: with `HostKeyAlias` set, OpenSSH uses
        the alias exactly and adds no port (openssh-portable sshconnect.c,
        `get_hostfile_hostname_ipaddr`),
        so a non-standard port is spelled the way known_hosts writes it, `[host]:port`."""
        host = self.remote.host or ""
        return host if self.remote.port == SSH_PORT else f"[{host}]:{self.remote.port}"

    @contextlib.contextmanager
    def _ssh_files(self) -> Iterator[tuple[Path | None, Path | None]]:
        """The deploy key and the pinned known_hosts as 0600 files for one command (SSH
        remotes), removed afterwards whatever happens."""
        if self.remote.kind != "ssh" or self._key is None or self._known_hosts is None:
            yield None, None
            return
        keys = self.data_dir / ".keys"
        keys.mkdir(parents=True, exist_ok=True, mode=0o700)
        stem = uuid.uuid4().hex
        key_path, hosts_path = keys / f"{stem}.key", keys / f"{stem}.known_hosts"
        try:
            _write_private(key_path, self._key)
            _write_private(hosts_path, self._known_hosts.encode())
            yield key_path, hosts_path
        finally:
            key_path.unlink(missing_ok=True)
            hosts_path.unlink(missing_ok=True)


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, data if data.endswith(b"\n") else data + b"\n")
    finally:
        os.close(fd)


def _probe_refusal(probe: CommandResult) -> Exception | None:
    """Why the write probe refuses the connection, or None when the server refused the
    dry-run push for want of write access (the one answer that proves the key read-only).
    A push that went through is a writable key; a host key that does not match, the
    network or DNS (unavailable: try again), or any other answer refuse it too."""
    if probe.returncode == 0:
        return WritableDeployKey()
    err = probe.stderr.lower()
    detail = (probe.stderr.strip().splitlines()[-1:] or [f"exit {probe.returncode}"])[0]
    if "host key verification failed" in err:
        return HostKeyChanged("connect")
    if any(failure in err for failure in _NETWORK_FAILURES):
        return AdapterUnavailable(GitReader.name, "connect", detail)
    if any(refused in err for refused in _WRITE_REFUSALS):
        return None
    return WriteProbeInconclusive(detail)
