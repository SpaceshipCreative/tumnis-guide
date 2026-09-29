"""`S3Storage`: MinIO, B2 or AWS through aioboto3 (P1-14)."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import ClassVar, Literal

from pydantic import BaseModel

from tumnis.core.adapters.base import Adapter, CallPolicy
from tumnis.core.clock import Clock
from tumnis.core.net import NetPolicy, Resolver, system_resolver
from tumnis.modules.knowledge.storage import FileStat, Health, Page

Answer = Literal["precondition_failed", "ignored"]


@dataclass(frozen=True)
class S3Config:
    endpoint: str | None  # None: AWS itself
    region: str
    access_key: str
    secret_key: str
    path_style: bool = True
    sse: Literal["AES256"] | None = None


class ConditionalWriteProbe(BaseModel, frozen=True):
    if_none_match_star: Answer
    stale_if_match: Answer

    @property
    def conditional_put(self) -> bool:
        return self.if_none_match_star == self.stale_if_match == "precondition_failed"


class S3Storage(Adapter):
    name: ClassVar[str] = "knowledge.s3"

    def __init__(
        self,
        config: S3Config,
        *,
        bucket: str,
        prefix: str = "",
        conditional_put: bool = True,
        health_write: bool = False,
        net_policy: NetPolicy | None = None,
        resolver: Resolver = system_resolver,
        clock: Clock | None = None,
        policy: CallPolicy | None = None,
    ) -> None:
        raise NotImplementedError

    async def aclose(self) -> None:
        raise NotImplementedError

    async def probe_conditional_writes(self) -> ConditionalWriteProbe:
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
