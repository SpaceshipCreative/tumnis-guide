"""`local_watch` (P1-15): a file changed inside a project folder on a watched local disk
queues a sync of its location; Tumnis's own `.tumnis/` does not; `stop` ends the watch."""

import asyncio
import contextlib
from pathlib import Path

import pytest

from tumnis.modules.knowledge import sync, workflows

WAIT_S = 10.0


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-15")
async def test_change_queues_a_sync_of_its_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notes = tmp_path / "share-a" / "Acme" / "notes"
    state = tmp_path / "share-b" / "Site" / ".tumnis"
    notes.mkdir(parents=True)
    state.mkdir(parents=True)
    queued: list[tuple[str, str]] = []
    seen = asyncio.Event()

    async def roots() -> list[tuple[str, str, str]]:
        return [
            ("ws-1", "loc-a", str(tmp_path / "share-a")),
            ("ws-1", "loc-b", str(tmp_path / "share-b")),  # only Tumnis's own files change
            ("ws-1", "loc-gone", str(tmp_path / "unmounted")),
        ]

    async def enqueue(workspace_id: str, location_id: str, *, workflow_id: str) -> None:
        assert workflow_id.startswith(f"folder-sync:{location_id}:watch:")
        queued.append((workspace_id, location_id))
        seen.set()

    monkeypatch.setattr(sync, "watched_roots", roots)
    monkeypatch.setattr(workflows, "enqueue_folder_sync", enqueue)
    monkeypatch.setattr(workflows, "WATCH_DEBOUNCE_MS", 100)
    monkeypatch.setattr(workflows, "WATCH_WAKE_MS", 100)

    stop = asyncio.Event()
    watch = asyncio.create_task(workflows.local_watch(stop))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + WAIT_S
    probe = 0
    # The watcher starts in its own time: change both shares until a sync is queued.
    while not seen.is_set() and loop.time() < deadline:
        probe += 1
        (state / "state.json").write_text(f'{{"n": {probe}}}')
        (notes / f"probe-{probe}.md").write_text("# Probe\n")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(seen.wait(), 0.5)
    stop.set()
    await asyncio.wait_for(watch, WAIT_S)

    assert seen.is_set()
    assert set(queued) == {("ws-1", "loc-a")}


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-15")
async def test_no_watched_location_waits_for_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    async def roots() -> list[tuple[str, str, str]]:
        return []

    monkeypatch.setattr(sync, "watched_roots", roots)
    stop = asyncio.Event()
    watch = asyncio.create_task(workflows.local_watch(stop))
    await asyncio.sleep(0)  # let the watcher reach its wait
    stop.set()
    await asyncio.wait_for(watch, WAIT_S)
    assert watch.done()
