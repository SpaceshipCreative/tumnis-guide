"""Imported by the kill test's worker subprocesses (P1-15): every location opens as a
server path whose landed writes are appended, one path per line, to the file
`TUMNIS_TEST_WRITE_LOG` names. The log outlives the killed worker, so the test counts every
write across both workers. (In fakes mode a location would otherwise be an in-memory tree
that dies with the worker.) No assertions live here."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

from tumnis.modules.knowledge import api
from tumnis.modules.knowledge.adapters.server_path import ServerPathStorage
from tumnis.modules.knowledge.storage import FileStat

LOG_ENV = "TUMNIS_TEST_WRITE_LOG"


class CountingStorage(ServerPathStorage):
    async def write(self, path: str, data: AsyncIterator[bytes], if_match: str | None) -> FileStat:
        written = await super().write(path, data, if_match)
        await asyncio.to_thread(_log, written.path)
        return written


def _log(path: str) -> None:
    with open(os.environ[LOG_ENV], "a", encoding="utf-8") as log:
        log.write(path + "\n")


def _counting(row: Any, _built: Any) -> CountingStorage:
    return CountingStorage(row["root"])


if os.environ.get(LOG_ENV):
    api.use_backend_hook(_counting)
