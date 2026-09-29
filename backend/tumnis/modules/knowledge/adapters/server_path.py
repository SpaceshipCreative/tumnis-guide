"""`ServerPathStorage`: a location on local disk or a mounted share (P1-14)."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import ClassVar

from tumnis.core.adapters.base import Adapter, CallPolicy
from tumnis.core.clock import Clock
from tumnis.modules.knowledge.storage import FileStat, Health, Page


class ServerPathStorage(Adapter):
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
        raise NotImplementedError

    async def stat(self, path: str) -> FileStat | None:
        raise NotImplementedError

    async def list(self, prefix: str, cursor: str | None) -> Page[FileStat]:
        raise NotImplementedError

    def read(self, path: str) -> AsyncIterator[bytes]:
        raise NotImplementedError

    async def write(self, path: str, data: AsyncIterator[bytes], if_match: str | None) -> FileStat:
        raise NotImplementedError

    async def move(self, src: str, dst: str) -> None:
        raise NotImplementedError

    async def delete(self, path: str) -> None:
        raise NotImplementedError

    async def health(self) -> Health:
        raise NotImplementedError
