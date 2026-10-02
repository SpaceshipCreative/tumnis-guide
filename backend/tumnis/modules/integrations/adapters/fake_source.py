"""`FakeSource`: the sync framework's scriptable connector (P3-02). Spec stub."""

from typing import Any

from tumnis.modules.integrations.adapters.fake import ScriptedConnector
from tumnis.modules.integrations.api import SyncPage

DEFAULT_PAGES: list[SyncPage] = []


def demo_pages(pages: int, per_page: int) -> list[SyncPage]:
    raise NotImplementedError


class FakeSource(ScriptedConnector):
    def __init__(self, *, clock: Any = None, **_: Any) -> None:
        super().__init__(provider="fake")
        self.started: list[Any] = []

    def script_errors(self, *errors: Exception) -> None:
        raise NotImplementedError
