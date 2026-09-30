"""`ClamAV`: the INSTREAM client for clamd (P1-16, SEC-10).

Protocol ([clamd](https://docs.clamav.net/manual/Usage/ClamdProtocol.html)): send
`zINSTREAM\\0`, then each chunk framed with a 4-byte big-endian length, then four zero
bytes; clamd answers `stream: OK`, `stream: <signature> FOUND` or `<reason> ERROR`, ending
with a NUL. An `ERROR` answer (most often the stream ran past clamd's StreamMaxLength) is a
failed scan, never a clean one.

The address is operator-configured infrastructure, like Postgres, so the client connects to
it directly and not through `resolve_and_check` (AGENTS.md, Scott decision 11, 2026-09-30):
this module is the one place that does.
"""

import asyncio
import contextlib
import struct
from collections.abc import AsyncIterator
from typing import ClassVar, Final

from tumnis.core.adapters.base import Adapter, CallPolicy
from tumnis.core.adapters.errors import AdapterRejected, AdapterUnavailable
from tumnis.core.adapters.retry import RetryPolicy
from tumnis.core.clock import Clock
from tumnis.modules.knowledge.adapters.port import ScanResult

FRAME_BYTES: Final = 1024 * 1024  # frames are cut to this; clamd allows up to StreamMaxLength
# A scan of a 50 MiB file takes a few seconds; the plan default is generous. One attempt: the
# stream cannot be replayed, so the workflow step retries the whole scan.
SCAN_POLICY: Final = CallPolicy(timeout_s=300, retry=RetryPolicy(max_attempts=1))
_INSTREAM: Final = b"zINSTREAM\0"
_END: Final = struct.pack(">I", 0)


class ClamAV(Adapter):
    name: ClassVar[str] = "knowledge.clamav"

    def __init__(
        self, host: str, port: int, *, clock: Clock, policy: CallPolicy = SCAN_POLICY
    ) -> None:
        super().__init__(policy=policy, clock=clock)
        self._host = host
        self._port = port

    async def scan(self, stream: AsyncIterator[bytes]) -> ScanResult:
        return await self.call("scan", lambda: self._scan(stream), idempotent=False)

    async def _scan(self, stream: AsyncIterator[bytes]) -> ScanResult:
        try:
            reader, writer = await asyncio.open_connection(self._host, self._port)
        except OSError as exc:
            raise AdapterUnavailable(self.name, "scan", f"cannot connect: {exc}") from exc
        try:
            await self._send(writer, _INSTREAM)
            async for chunk in stream:  # a failing source raises its own error, untranslated
                for start in range(0, len(chunk), FRAME_BYTES):
                    frame = chunk[start : start + FRAME_BYTES]
                    await self._send(writer, struct.pack(">I", len(frame)) + frame)
            await self._send(writer, _END)
            try:
                reply = await reader.readuntil(b"\0")
            except (OSError, asyncio.IncompleteReadError) as exc:
                raise AdapterUnavailable(self.name, "scan", f"no answer: {exc!r}") from exc
        finally:
            writer.close()
            with contextlib.suppress(OSError):  # the answer, or the failure, is in hand
                await writer.wait_closed()
        return self._parse(reply)

    async def _send(self, writer: asyncio.StreamWriter, data: bytes) -> None:
        try:
            writer.write(data)
            await writer.drain()
        except OSError as exc:
            raise AdapterUnavailable(self.name, "scan", f"connection lost: {exc!r}") from exc

    def _parse(self, reply: bytes) -> ScanResult:
        text = reply.rstrip(b"\0\n ").decode(errors="replace")
        if text.endswith(" OK"):
            return ScanResult(infected=False)
        if text.endswith(" FOUND"):
            signature = text.removeprefix("stream:").removesuffix(" FOUND").strip()
            return ScanResult(infected=True, signature=signature or "unknown")
        raise AdapterRejected(self.name, "scan", f"clamd answered: {text[:200]}")
