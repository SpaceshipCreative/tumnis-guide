"""`ScriptedConnector`: a scriptable connector for tests and fake mode (P0-12).

`script_pages([...])` queues the pages `sync` hands out in order (then one empty, final
page); `calls` records every `sync` cursor. `map` turns the scripted payloads into
message, thread, note, person and artifact records (pure); `mapper=` replaces it, for
tests that need records of another type.
"""

from collections.abc import Callable, Iterable, Sequence
from typing import Any, ClassVar

from tumnis.core.adapters.registry import Health
from tumnis.core.canonical import CanonicalRecord
from tumnis.modules.integrations.api import Capability, ConnectorKind, RawItem, SyncPage

Mapper = Callable[[RawItem], Sequence[CanonicalRecord]]


class ScriptedConnector:
    kind: ConnectorKind = "email"
    provider: str = "scripted"
    capabilities: frozenset[Capability] = frozenset({"poll", "read"})
    PAGE_LIMIT: ClassVar[int] = 1000

    def __init__(
        self,
        *,
        kind: ConnectorKind = "email",
        provider: str = "scripted",
        mapper: Mapper | None = None,
    ) -> None:
        self.kind = kind
        self.provider = provider
        self._mapper = mapper
        self._pages: list[SyncPage] = []
        self.calls: list[dict[str, Any] | None] = []

    def script_pages(self, pages: Iterable[SyncPage]) -> None:
        raise NotImplementedError

    async def sync(self, cursor: dict[str, Any] | None) -> SyncPage:
        raise NotImplementedError

    def map(self, raw: RawItem) -> list[CanonicalRecord]:
        raise NotImplementedError

    async def health(self) -> Health:
        raise NotImplementedError
