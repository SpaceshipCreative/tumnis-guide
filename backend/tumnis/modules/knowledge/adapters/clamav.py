"""`ClamAV`: the INSTREAM client for clamd (P1-16, SEC-10)."""

from collections.abc import AsyncIterator
from typing import ClassVar

from tumnis.core.adapters.base import Adapter, CallPolicy
from tumnis.core.clock import Clock
from tumnis.modules.knowledge.adapters.port import ScanResult


class ClamAV(Adapter):
    name: ClassVar[str] = "knowledge.clamav"

    def __init__(self, host: str, port: int, *, clock: Clock, policy: CallPolicy | None = None):
        raise NotImplementedError

    async def scan(self, stream: AsyncIterator[bytes]) -> ScanResult:
        raise NotImplementedError
