"""`SftpStorage`: a location on an SFTP server, over asyncssh (P3-14, FR-15.7, FR-15.12).

Red-phase seam: the spec tests turn it green.
"""

# The names are the plan's shared contract (P3-14 interfaces), not "...Error".
# ruff: noqa: N818

from collections.abc import AsyncIterator
from typing import ClassVar

from pydantic import BaseModel

from tumnis.core.adapters.base import Adapter as AdapterBase
from tumnis.core.adapters.base import CallPolicy
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.net import NetPolicy, Resolver, system_resolver
from tumnis.modules.knowledge.storage import FileStat, Health, Page, StorageError


class HostKeyChanged(StorageError):
    """The server presented a host key other than the pinned one; nothing was sent."""


class ProbedKey(BaseModel, frozen=True):
    openssh: str  # "ssh-ed25519 AAAA..." as known_hosts has it
    sha256: str  # "SHA256:..." as `ssh-keygen -lf` shows it


async def probe_host_key(
    host: str, port: int, *, net_policy: NetPolicy, resolver: Resolver = system_resolver
) -> ProbedKey:
    raise NotImplementedError("P3-14")


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
        super().__init__(policy=policy or CallPolicy(timeout_s=120.0), clock=clock or SystemClock())
        self.host, self.port, self.username, self.root = host, port, username, root

    async def stat(self, path: str) -> FileStat | None:
        raise NotImplementedError("P3-14")

    async def list(self, prefix: str, cursor: str | None) -> Page[FileStat]:
        raise NotImplementedError("P3-14")

    async def read(self, path: str) -> AsyncIterator[bytes]:
        raise NotImplementedError("P3-14")
        yield b""  # pragma: no cover

    async def write(self, path: str, data: AsyncIterator[bytes], if_match: str | None) -> FileStat:
        raise NotImplementedError("P3-14")

    async def move(self, src: str, dst: str) -> None:
        raise NotImplementedError("P3-14")

    async def delete(self, path: str) -> None:
        raise NotImplementedError("P3-14")

    async def ensure_folder(self, path: str) -> None:
        raise NotImplementedError("P3-14")

    async def health(self) -> Health:
        raise NotImplementedError("P3-14")

    async def aclose(self) -> None:
        return None
