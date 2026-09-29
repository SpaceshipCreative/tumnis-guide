"""The cache probe as a second process (P0-08, T-P0-08-05, T-P0-08-06, T-P0-08-19).

`python -m tumnis.testing.cache_probe --dsn <libpq> --key <cache key>` caches the key in its
own InProcessCache with its own invalidation listener, prints `ready`, then polls every
20 ms and prints `gone <ms>` once the key is no longer cached.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass

READY_TIMEOUT_S = 30.0


@dataclass
class Probe:
    process: asyncio.subprocess.Process

    async def line(self, wait_s: float) -> str | None:
        """The probe's next output line, or None when none arrives within `wait_s`."""
        assert self.process.stdout is not None
        try:
            raw = await asyncio.wait_for(self.process.stdout.readline(), wait_s)
        except TimeoutError:
            return None
        if not raw:
            raise RuntimeError(f"cache probe exited: {await self._stderr()}")
        return raw.decode().strip()

    async def _stderr(self) -> str:
        assert self.process.stderr is not None
        await self.process.wait()
        return (await self.process.stderr.read()).decode()


@contextlib.asynccontextmanager
async def cache_probe(dsn: str, key: str) -> AsyncIterator[Probe]:
    """Start the probe and wait for `ready`; kill it on exit."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tumnis.testing.cache_probe",
        "--dsn",
        dsn,
        "--key",
        key,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    probe = Probe(process)
    try:
        first = await probe.line(READY_TIMEOUT_S)
        if first != "ready":
            raise RuntimeError(f"cache probe did not get ready: {first!r}")
        yield probe
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
