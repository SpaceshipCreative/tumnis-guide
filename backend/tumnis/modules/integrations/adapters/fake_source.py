"""`FakeSource`: the sync framework's scriptable connector (provider `fake`, P3-02).

It is stateless across instances: page `i` (0-based, `cursor["page"]`, absent on a first
cursor) is `DEFAULT_PAGES[i]`, read at call time, so a test or a worker subprocess can set
the pages once for every instance (`fake_source.DEFAULT_PAGES[:] = demo_pages(4, 2)`).
Past the last page it answers one empty, final page. `script_errors(...)` queues errors
the next `sync` calls raise, one each (an `AdapterUnavailable` for an HTTP 500, a
`ReauthRequired` for a 401). `started` records the clock's time of every `sync` call,
taken before any await, so a test can check the provider's request limit.

Mapping is the scripted connector's (message, note, person and artifact payloads).
"""

from datetime import UTC, datetime
from typing import Any, Final

from tumnis.core.clock import Clock
from tumnis.modules.integrations.adapters.fake import ScriptedConnector
from tumnis.modules.integrations.api import RawItem, SyncPage

DEFAULT_PAGES: list[SyncPage] = []
DEMO_FETCHED_AT: Final = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


def demo_pages(pages: int, per_page: int) -> list[SyncPage]:
    """`pages` pages of `per_page` messages each, external ids `msg-p<page>-<n>` (both
    1-based); page i's next cursor is `{"page": i + 1}` and the last page has none."""
    built = []
    for index in range(pages):
        page = index + 1
        items = [
            RawItem(
                external_id=f"msg-p{page}-{n}",
                record_type="message",
                fetched_at=DEMO_FETCHED_AT,
                payload={
                    "id": f"msg-p{page}-{n}",
                    "from": "sender@example.com",
                    "to": ["me@example.org"],
                    "subject": f"Demo message {page}.{n}",
                    "text": "Demo",
                    "sent_at": DEMO_FETCHED_AT.isoformat(),
                },
            )
            for n in range(1, per_page + 1)
        ]
        last = page == pages
        built.append(
            SyncPage(
                items=items,
                next_cursor=None if last else {"page": page},
                has_more=not last,
            )
        )
    return built


class FakeSource(ScriptedConnector):
    def __init__(self, *, clock: Clock | None = None, **_: Any) -> None:
        super().__init__(provider="fake")
        self._clock = clock
        self._errors: list[Exception] = []
        self.started: list[datetime] = []

    def script_errors(self, *errors: Exception) -> None:
        """The next `len(errors)` syncs raise these, in order."""
        self._errors.extend(errors)

    async def sync(self, cursor: dict[str, Any] | None) -> SyncPage:
        if self._clock is not None:
            self.started.append(self._clock.now())
        self.calls.append(cursor)
        if self._errors:
            raise self._errors.pop(0)
        if self._pages:
            return self._pages.pop(0)
        index = int((cursor or {}).get("page", 0))
        if 0 <= index < len(DEFAULT_PAGES):
            return DEFAULT_PAGES[index]
        return SyncPage(items=[], next_cursor=None, has_more=False)
