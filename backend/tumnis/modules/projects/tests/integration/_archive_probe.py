"""Imported by the archive kill test's worker subprocesses (P2-18): every location opens as
a server path, and every file written or deleted outside a project folder (the packed
folder) is appended, one `<op> <path>` per line, to the file `TUMNIS_TEST_ARCHIVE_LOG`
names. The log outlives a killed worker, so the test counts every pack written across both
workers. No assertions live here."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

from tumnis.modules.knowledge import api
from tumnis.modules.knowledge.adapters.server_path import ServerPathStorage
from tumnis.modules.knowledge.storage import FileStat

LOG_ENV = "TUMNIS_TEST_ARCHIVE_LOG"


class LoggingStorage(ServerPathStorage):
    async def write(self, path: str, data: AsyncIterator[bytes], if_match: str | None) -> FileStat:
        written = await super().write(path, data, if_match)
        await asyncio.to_thread(_log, f"write {written.path}")
        return written

    async def delete(self, path: str) -> None:
        await super().delete(path)
        await asyncio.to_thread(_log, f"delete {path}")


def _log(line: str) -> None:
    with open(os.environ[LOG_ENV], "a", encoding="utf-8") as log:
        log.write(line + "\n")


def _logging(row: Any, _built: Any) -> LoggingStorage:
    return LoggingStorage(row["root"])


if os.environ.get(LOG_ENV):
    api.use_backend_hook(_logging)
