"""Receiving an upload (P1-16, SEC-10): the multipart body is parsed as it arrives and the
file part goes straight to `<spool>/<version_id>`, hashed and counted on the way, so a body
over the limit is refused (413 `too_large`) the moment it passes `MAX_UPLOAD_BYTES` and
never sits in memory. The client's `Content-Type` for the file is not read at all: the type
comes from the content (`pipeline.sniff`).

`spool_upload` is HTTP plumbing for `router.py`; the rows and the workflow are `api.py`'s.
"""

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final
from uuid import UUID

from fastapi import Request
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header

from tumnis.core.errors import ProblemError
from tumnis.modules.knowledge.rules import MAX_UPLOAD_BYTES

MAX_FIELD_BYTES: Final = 64 * 1024  # a text field (project_id, title) is a few bytes
FILE_FIELD: Final = "file"


@dataclass
class Spooled:
    """What the body held: its text fields and, if there was a file part, the file's
    name, size and sha256 (it is at the path given to `spool_upload`)."""

    fields: dict[str, str] = field(default_factory=dict)
    name: str | None = None
    size: int = 0
    sha256: str = ""


class _Collector:
    """The parser's callbacks, which are synchronous: they only note what happened, and
    `spool_upload` acts on the notes between chunks."""

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self._headers: dict[bytes, bytes] = {}
        self._field = b""
        self._value = b""

    def on_part_begin(self) -> None:
        self._headers = {}

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._field += data[start:end]

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._value += data[start:end]

    def on_header_end(self) -> None:
        self._headers[self._field.lower()] = self._value
        self._field = self._value = b""

    def on_headers_finished(self) -> None:
        self.events.append(("start", self._headers))

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        self.events.append(("data", bytes(data[start:end])))

    def on_part_end(self) -> None:
        self.events.append(("end", None))

    def callbacks(self) -> dict[str, Callable[..., None]]:
        return {
            "on_part_begin": self.on_part_begin,
            "on_header_field": self.on_header_field,
            "on_header_value": self.on_header_value,
            "on_header_end": self.on_header_end,
            "on_headers_finished": self.on_headers_finished,
            "on_part_data": self.on_part_data,
            "on_part_end": self.on_part_end,
        }


def _bad(detail: str) -> ProblemError:
    return ProblemError(422, "invalid_upload", detail)


def _boundary(request: Request) -> bytes:
    kind, params = parse_options_header(request.headers.get("content-type", ""))
    boundary = params.get(b"boundary")
    if kind != b"multipart/form-data" or not boundary:
        raise _bad("The body must be multipart/form-data with a file part")
    return boundary


class _Sink:
    """Where the parsed parts go: text fields into `result.fields`, the file part to disk."""

    def __init__(
        self,
        dest: Path,
        result: Spooled,
        on_file: Callable[[dict[str, str]], Awaitable[None]],
    ) -> None:
        self.dest = dest
        self.result = result
        self.on_file = on_file
        self.digest = hashlib.sha256()
        self.handle: Any = None  # the open spool file while the file part is being read
        self.field: str | None = None  # the current text field; None in the file part
        self.text = b""

    async def start(self, headers: dict[bytes, bytes]) -> None:
        _, params = parse_options_header(headers.get(b"content-disposition", b""))
        name = params.get(b"name", b"").decode(errors="replace")
        if name == FILE_FIELD and self.result.name is not None:
            raise _bad("The body has more than one file part")
        if name == FILE_FIELD:
            await self.on_file(self.result.fields)
            self.result.name = params.get(b"filename", b"").decode(errors="replace") or "upload"
            self.handle = await asyncio.to_thread(self.dest.open, "wb")
            self.field = None
        else:
            self.field, self.text = name, b""

    async def data(self, value: bytes) -> None:
        if self.field is not None:
            self.text += value
            if len(self.text) > MAX_FIELD_BYTES:
                raise _bad("A form field is too long")
        elif self.handle is not None:
            self.result.size += len(value)
            if self.result.size > MAX_UPLOAD_BYTES:
                raise ProblemError(413, "too_large", "The file is over 50 MiB.")
            self.digest.update(value)
            await asyncio.to_thread(self.handle.write, value)

    async def end(self) -> None:
        if self.field is not None:
            self.result.fields[self.field] = self.text.decode(errors="replace")
        await self.close()
        self.field, self.text = None, b""

    async def close(self) -> None:
        if self.handle is not None:
            handle, self.handle = self.handle, None
            await asyncio.to_thread(handle.close)


async def spool_upload(
    request: Request,
    dest: Path,
    *,
    on_file: Callable[[dict[str, str]], Awaitable[None]],
) -> Spooled:
    """Read the request body into `dest` (the file part) and `Spooled.fields`.

    `on_file` runs once when the file part starts, with the text fields seen so far (a
    client that sends `project_id` first is answered before its bytes are read). The file
    at `dest` is removed again on any failure; the caller removes it if a later step fails.
    """
    result = Spooled()
    collector = _Collector()
    parser = MultipartParser(_boundary(request), collector.callbacks())  # type: ignore[arg-type]
    sink = _Sink(dest, result, on_file)
    await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)
    try:
        async for chunk in request.stream():
            try:
                parser.write(chunk)
            except MultipartParseError:
                raise _bad("The body is not valid multipart/form-data") from None
            for kind, value in collector.events:
                if kind == "start":
                    await sink.start(value)
                elif kind == "data":
                    await sink.data(value)
                else:
                    await sink.end()
            collector.events.clear()
        try:
            parser.finalize()
        except MultipartParseError:
            raise _bad("The body is not valid multipart/form-data") from None
        if result.name is None:
            raise _bad("The body has no file part")
    except BaseException:
        await sink.close()
        await asyncio.to_thread(dest.unlink, missing_ok=True)
        raise
    result.sha256 = sink.digest.hexdigest()
    return result


def parse_project_id(raw: str | None) -> UUID | None:
    """The `project_id` field: absent or empty is the workspace knowledge base."""
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        raise _bad("project_id is not an id") from None
