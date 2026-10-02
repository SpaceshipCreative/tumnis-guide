"""Disposable service containers for integration tests (R-17): MinIO, SFTP, clamd.

Created here, reused by P0-28, P1-14, P1-16 and P3-14, never redefined. Session scope
per xdist worker and lazy: a container starts only when a test asks for it. Images are
pinned by digest.

- MinIO: the minio/minio Docker Hub images are no longer published, so the fixture runs
  chainguard/minio (the same server binary, multi-arch) through testcontainers' MinIO module.
- SFTP: atmoz/sftp is amd64 only; on arm64 hosts Docker runs it under emulation.
- clamd: clamav/clamav-debian (multi-arch) ships signatures in the image; freshclam is
  switched off so the fixture needs no network. Loading signatures still takes a while.
"""

from __future__ import annotations

import asyncio
import base64
import shlex
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

MINIO_IMAGE = (
    "chainguard/minio@sha256:71674988a1c7ddd5724928633199152b11e4ddefd6c6ce2d60772ff4a8f22ca9"
)
SFTP_IMAGE = (
    "atmoz/sftp@sha256:a81ea210713555be76075b4b2788a4addfaa54d137cd881f3a99ac539f0be2c5"  # alpine
)
CLAMD_IMAGE = (
    "clamav/clamav-debian@sha256:9bb8712a50f0e75166e936c452cd82dd5e5be0b85586598930b5bbb84a99a578"
)
SFTP_USER = "tumnis"
SFTP_HOME = f"/home/{SFTP_USER}"  # the user's chroot
SFTP_UID = 1001
HOST_KEY = "/etc/ssh/ssh_host_ed25519_key"


@dataclass(frozen=True)
class S3Endpoint:
    url: str
    access_key: str
    secret_key: str
    region: str = "us-east-1"


@dataclass(frozen=True)
class SftpEndpoint:
    """The SFTP container. The user is chrooted to its home, so a path the user calls
    `upload/x` is `SFTP_HOME/upload/x` on the server. P3-14 adds `rotate_host_key` and
    `plant_symlink`, which act inside the container as root."""

    host: str
    port: int
    user: str
    private_key_path: Path
    host_key: str  # "ssh-ed25519 AAAA..." as it goes in known_hosts
    container: Any = field(default=None, compare=False, repr=False)

    def exec(self, command: str) -> str:
        """`command` run by `sh -c` in the container as root; its output (it must succeed)."""
        result = self.container.exec(["sh", "-c", command])
        if result.exit_code != 0:
            raise RuntimeError(f"{command!r} failed: {result.output!r}")
        return str(result.output.decode())

    def server_path(self, path: str) -> str:
        """Where a path the user sees (relative to its chroot) lies on the server."""
        return f"{SFTP_HOME}/{path.lstrip('/')}"

    def plant_symlink(self, path: str, target: str) -> None:
        """A symlink at the user's `path` pointing at `target` (as the server sees it),
        planted behind Tumnis's back and owned by the user."""
        link = shlex.quote(self.server_path(path))
        self.exec(
            f'mkdir -p "$(dirname {link})" && ln -s {shlex.quote(target)} {link}'
            f" && chown -h {SFTP_UID} {link}"
        )

    @asynccontextmanager
    async def rotate_host_key(self) -> AsyncIterator[str]:
        """Give the server a new ed25519 host key (sshd reloads its keys on SIGHUP); yields
        the new key as known_hosts has it. The original key comes back on exit, so the
        session's other tests keep their pinned key. Both keys are made inside the
        container at test time."""
        original = base64.b64encode(self.exec(f"cat {HOST_KEY}").encode()).decode()
        self.exec(
            f"rm -f {HOST_KEY} {HOST_KEY}.pub && ssh-keygen -q -t ed25519 -N '' -f {HOST_KEY}"
            " && kill -HUP 1"
        )
        try:
            yield await self._served_key(lambda served: served != self.host_key)
        finally:
            self.exec(
                f"echo {original} | base64 -d > {HOST_KEY} && chmod 600 {HOST_KEY}"
                f" && ssh-keygen -y -f {HOST_KEY} > {HOST_KEY}.pub && kill -HUP 1"
            )
            await self._served_key(lambda served: served == self.host_key)

    async def _served_key(self, wanted: Any) -> str:
        """The ed25519 key sshd presents, once `wanted(key)` holds (sshd restarts on
        SIGHUP, so it may not answer for a moment)."""
        import asyncssh  # noqa: PLC0415

        loop = asyncio.get_running_loop()
        deadline = loop.time() + 30
        while True:
            try:
                key = await asyncssh.get_server_host_key(
                    self.host, self.port, server_host_key_algs=["ssh-ed25519"], config=None
                )
            except (OSError, asyncssh.Error):
                key = None
            if key is not None:
                key_type, key_data, *_ = key.export_public_key().decode().split()
                served = f"{key_type} {key_data}"
                if wanted(served):
                    return served
            if loop.time() > deadline:
                raise RuntimeError("sshd did not reload its host key")
            await asyncio.sleep(0.2)


@dataclass(frozen=True)
class ClamdEndpoint:
    host: str
    port: int


@dataclass(frozen=True)
class KeyPair:
    private_path: Path
    public_path: Path


def make_ed25519_keypair(folder: Path) -> KeyPair:
    """An OpenSSH ed25519 key pair written to `folder` (cryptography, no network)."""
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: PLC0415
        Ed25519PrivateKey,
    )

    key = Ed25519PrivateKey.generate()
    private_path, public_path = folder / "id_ed25519", folder / "id_ed25519.pub"
    private_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
    )
    private_path.chmod(0o600)
    public_path.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
        + b" tumnis-test\n"
    )
    return KeyPair(private_path, public_path)


@pytest.fixture(scope="session")
def minio() -> Iterator[S3Endpoint]:
    from testcontainers.community.minio import MinioContainer  # noqa: PLC0415

    container = MinioContainer(MINIO_IMAGE)
    # Current MinIO reads the root credentials from these; the module sets the old names.
    container.with_env("MINIO_ROOT_USER", container.access_key)
    container.with_env("MINIO_ROOT_PASSWORD", container.secret_key)
    with container as m:
        cfg = m.get_config()  # endpoint, access_key, secret_key
        yield S3Endpoint(f"http://{cfg['endpoint']}", cfg["access_key"], cfg["secret_key"])


def read_host_key(container: object) -> str:
    """The server's ed25519 host key, "type base64", for a pinned known_hosts entry."""
    result = container.exec("cat /etc/ssh/ssh_host_ed25519_key.pub")  # type: ignore[attr-defined]
    if result.exit_code != 0:
        raise RuntimeError(f"cannot read the SFTP host key: {result.output!r}")
    key_type, key_data, *_ = result.output.decode().split()
    return f"{key_type} {key_data}"


@pytest.fixture(scope="session")
def sftp_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SftpEndpoint]:
    from testcontainers.core.container import DockerContainer  # noqa: PLC0415
    from testcontainers.core.wait_strategies import LogMessageWaitStrategy  # noqa: PLC0415

    key = make_ed25519_keypair(tmp_path_factory.mktemp("sftp"))
    container = (
        DockerContainer(SFTP_IMAGE)
        # user:password:uid:gid:dirs - key-only login, uid 1001, an upload/ folder.
        .with_command(f"{SFTP_USER}::1001::upload")
        .with_volume_mapping(str(key.public_path), f"/home/{SFTP_USER}/.ssh/keys/id.pub", "ro")
        .with_exposed_ports(22)
        .waiting_for(LogMessageWaitStrategy("Server listening"))
    )
    with container as c:
        yield SftpEndpoint(
            c.get_container_host_ip(),
            int(c.get_exposed_port(22)),
            SFTP_USER,
            key.private_path,
            read_host_key(c),
            container=c,
        )


@pytest.fixture(scope="session")
def clamd() -> Iterator[ClamdEndpoint]:
    from testcontainers.core.container import DockerContainer  # noqa: PLC0415
    from testcontainers.core.wait_strategies import LogMessageWaitStrategy  # noqa: PLC0415

    container = (
        DockerContainer(CLAMD_IMAGE)
        .with_env("CLAMAV_NO_FRESHCLAMD", "true")
        .with_exposed_ports(3310)
        # Signature load is slow; the init script prints this once clamd's socket is up.
        .waiting_for(
            LogMessageWaitStrategy("socket found, clamd started").with_startup_timeout(300)
        )
    )
    with container as c:
        yield ClamdEndpoint(c.get_container_host_ip(), int(c.get_exposed_port(3310)))
