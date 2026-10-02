"""Imported by the kill test's worker subprocesses (`run_worker --import`): the `fake`
connector hands out four pages of two messages each, and every page it serves is appended
to the file named by `INTEGRATIONS_FAKE_PAGE_LOG` (one line per fetch: the page number,
1-based), so the test sees across both processes which pages were fetched (T-P3-02-05)."""

import os
from typing import Any

import tumnis.modules.integrations.workflows  # noqa: F401  # registers the workflows
from tumnis.modules.integrations.adapters import fake_source
from tumnis.modules.integrations.adapters.fake_source import FakeSource, demo_pages

PAGES = 4
PER_PAGE = 2

fake_source.DEFAULT_PAGES[:] = demo_pages(PAGES, PER_PAGE)

_served = FakeSource.sync


async def _logged(self: FakeSource, cursor: dict[str, Any] | None) -> Any:
    index = int((cursor or {}).get("page", 0))
    with open(os.environ["INTEGRATIONS_FAKE_PAGE_LOG"], "a") as log:  # noqa: ASYNC230
        log.write(f"{index + 1}\n")
    return await _served(self, cursor)


FakeSource.sync = _logged  # type: ignore[method-assign,assignment]
