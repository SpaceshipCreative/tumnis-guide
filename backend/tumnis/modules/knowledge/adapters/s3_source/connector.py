"""`S3SourceConnector`: a linked bucket read through P1-14's `S3Storage` (P3-13, FR-15.11).

It composes the storage adapter over the whole bucket (no location prefix, no conditional
puts) and exposes only the reads, so a linked source can never write, move or delete: the
key is read-only anyway, and this class has no method that would try. The SSRF guard is
`S3Storage`'s: the endpoint is checked when the client is made and every connection goes
to the checked address.
"""

from collections.abc import AsyncIterator
from typing import ClassVar

from tumnis.core.clock import Clock
from tumnis.core.net import NetPolicy, Resolver, system_resolver
from tumnis.modules.knowledge.adapters.s3 import S3Config, S3Storage
from tumnis.modules.knowledge.storage import FileStat, Health, Page


class S3SourceConnector:
    name: ClassVar[str] = "knowledge.s3_source"

    def __init__(
        self,
        config: S3Config,
        *,
        bucket: str,
        net_policy: NetPolicy | None = None,
        resolver: Resolver = system_resolver,
        clock: Clock | None = None,
    ) -> None:
        self.bucket = bucket
        self._storage = S3Storage(
            config,
            bucket=bucket,
            prefix="",
            conditional_put=False,
            health_write=False,
            net_policy=net_policy,
            resolver=resolver,
            clock=clock,
        )

    async def list(self, prefix: str, cursor: str | None) -> Page[FileStat]:
        return await self._storage.list(prefix, cursor)

    async def stat(self, key: str) -> FileStat | None:
        return await self._storage.stat(key)

    def read(self, key: str) -> AsyncIterator[bytes]:
        return self._storage.read(key)

    async def health(self) -> Health:
        return await self._storage.health()

    async def aclose(self) -> None:
        await self._storage.aclose()
