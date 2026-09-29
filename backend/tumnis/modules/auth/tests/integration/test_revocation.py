"""A revoked key dies in every process within a second (P0-14, SEC-2, Caching)."""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import PepperFile, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

REFUSED_WITHIN_MS = 1_000
READY_TIMEOUT_S = 30.0


@pytest.fixture
async def core_db(db: DbUrls) -> AsyncIterator[None]:
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    try:
        yield
    finally:
        await core_db.dispose()


async def _line(process: asyncio.subprocess.Process, wait_s: float) -> str | None:
    assert process.stdout is not None
    try:
        raw = await asyncio.wait_for(process.stdout.readline(), wait_s)
    except TimeoutError:
        return None
    if not raw:
        assert process.stderr is not None
        await process.wait()
        raise RuntimeError(f"key probe exited: {(await process.stderr.read()).decode()}")
    return raw.decode().strip()


@contextlib.asynccontextmanager
async def key_probe(db: DbUrls, pepper: str, key: str) -> AsyncIterator[asyncio.subprocess.Process]:
    """`python -m tumnis.testing.key_probe`, started and past its first `ok`."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tumnis.testing.key_probe",
        "--database-url",
        db.app,
        "--pepper-file",
        pepper,
        "--key",
        key,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        first = await _line(process, READY_TIMEOUT_S)
        if first != "ok":
            raise RuntimeError(f"key probe did not authenticate: {first!r}")
        yield process
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.req("SEC-2", "Caching")
@pytest.mark.wp("P0-14")
@pytest.mark.usefixtures("core_db", "master_key_file")
async def test_revoked_key_fails_in_every_process_within_one_second(
    db: DbUrls,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    pepper_file: PepperFile,
) -> None:
    """T-P0-14-04
    A second process that authenticated the key (and so has it cached) refuses it less
    than 1,000 ms after the revoke commits.
    """
    from tumnis.modules.auth import api  # noqa: PLC0415

    created = await api.create_key(
        workspace.ctx, api.KeyIn(name="probe", scopes=["tasks:read"]), now=clock.now()
    )
    async with key_probe(db, str(pepper_file.path), created.key) as process:
        await api.revoke_key(workspace.ctx, created.id, now=clock.now())
        committed = time.monotonic()
        line = await _line(process, wait_s=5.0)
        elapsed_ms = (time.monotonic() - committed) * 1000

    assert line is not None
    assert line.startswith("refused"), line
    assert elapsed_ms < REFUSED_WITHIN_MS, elapsed_ms
