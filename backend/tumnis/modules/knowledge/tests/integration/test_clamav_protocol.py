"""The bytes `ClamAV` puts on the wire and how it reads clamd's answers (P1-16, SEC-10),
against a scripted server on loopback: no container needed. The real clamd is the contract
suite's job (T-P1-16-05); this pins the framing and the failure answers."""

from __future__ import annotations

import asyncio
import struct
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from tumnis.core.adapters.errors import AdapterRejected, AdapterUnavailable
from tumnis.core.clock import FixedClock
from tumnis.modules.knowledge.adapters.clamav import FRAME_BYTES, ClamAV
from tumnis.modules.knowledge.tests._samples import stream

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

CLOCK = FixedClock(datetime(2026, 3, 9, 12, 0, tzinfo=UTC))


class Clamd:
    """A one-connection-at-a-time server that records what a client sent and answers with
    `reply`."""

    def __init__(self, reply: bytes) -> None:
        self.reply = reply
        self.received: list[bytes] = []
        self._server: asyncio.Server | None = None

    async def __aenter__(self) -> Clamd:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    @property
    def port(self) -> int:
        assert self._server is not None
        return int(self._server.sockets[0].getsockname()[1])

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        assert await reader.readexactly(10) == b"zINSTREAM\0"
        while (size := struct.unpack(">I", await reader.readexactly(4))[0]) > 0:
            self.received.append(await reader.readexactly(size))
        writer.write(self.reply)
        await writer.drain()
        writer.close()


@pytest.mark.req("SEC-10")
@pytest.mark.wp("P1-16")
async def test_frames_carry_length_prefixes_and_end_with_zero() -> None:
    """Chunks go out in frames of at most FRAME_BYTES, in order, and the stream ends with
    a zero length; `stream: OK` is a clean scan."""
    body = bytes(range(256)) * 12_000 + b"tail"  # over one frame
    async with Clamd(b"stream: OK\0") as clamd:
        result = await ClamAV("127.0.0.1", clamd.port, clock=CLOCK).scan(
            stream(body, size=len(body))
        )
    assert result.infected is False
    assert b"".join(clamd.received) == body
    assert max(len(frame) for frame in clamd.received) <= FRAME_BYTES


@pytest.mark.req("SEC-10")
@pytest.mark.wp("P1-16")
async def test_found_reply_names_the_signature() -> None:
    """`stream: <name> FOUND` is an infected scan carrying the name."""
    async with Clamd(b"stream: Win.Test.EICAR_HDB-1 FOUND\0") as clamd:
        result = await ClamAV("127.0.0.1", clamd.port, clock=CLOCK).scan(stream(b"x"))
    assert (result.infected, result.signature) == (True, "Win.Test.EICAR_HDB-1")


@pytest.mark.req("SEC-10")
@pytest.mark.wp("P1-16")
async def test_error_reply_is_a_failed_scan_never_clean() -> None:
    """clamd's `INSTREAM size limit exceeded. ERROR` (and any other ERROR) is refused, not
    read as clean."""
    async with Clamd(b"INSTREAM size limit exceeded. ERROR\0") as clamd:
        with pytest.raises(AdapterRejected):
            await ClamAV("127.0.0.1", clamd.port, clock=CLOCK).scan(stream(b"x"))


@pytest.mark.req("SEC-10")
@pytest.mark.wp("P1-16")
async def test_unreachable_clamd_is_unavailable() -> None:
    """A refused connection is `AdapterUnavailable` (the step retries), never a result."""
    async with Clamd(b"") as clamd:
        port = clamd.port
    with pytest.raises(AdapterUnavailable):
        await ClamAV("127.0.0.1", port, clock=CLOCK).scan(stream(b"x"))


@pytest.mark.req("SEC-10")
@pytest.mark.wp("P1-16")
async def test_a_stream_that_fails_midway_fails_the_scan() -> None:
    """A source that raises while streaming ends the scan with its error; clamd is not left
    with a clean verdict."""

    async def broken() -> AsyncIterator[bytes]:
        yield b"first"
        raise OSError("disk went away")

    async with Clamd(b"stream: OK\0") as clamd:
        with pytest.raises(OSError, match="disk went away"):
            await ClamAV("127.0.0.1", clamd.port, clock=CLOCK).scan(broken())
