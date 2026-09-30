"""`local_watch` (P1-15): a file changed inside a project folder on a watched local disk
queues one sync of its location; Tumnis's own `.tumnis/` does not; `stop` ends the watch."""

import asyncio
from pathlib import Path

import pytest

from tumnis.modules.knowledge import sync, workflows

WAIT_S = 10.0


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-15")
async def test_change_queues_one_sync_of_its_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "share"
    (root / "Acme" / ".tumnis").mkdir(parents=True)
    queued: list[tuple[str, str]] = []
    seen = asyncio.Event()

    async def roots() -> list[tuple[str, str, str]]:
        return [("ws-1", "loc-a", str(root)), ("ws-1", "loc-gone", str(tmp_path / "unmounted"))]

    async def enqueue(workspace_id: str, location_id: str, *, workflow_id: str) -> None:
        assert workflow_id.startswith(f"folder-sync:{location_id}:watch:")
        queued.append((workspace_id, location_id))
        seen.set()

    monkeypatch.setattr(sync, "watched_roots", roots)
    monkeypatch.setattr(workflows, "enqueue_folder_sync", enqueue)
    monkeypatch.setattr(workflows, "WATCH_DEBOUNCE_MS", 200)
    monkeypatch.setattr(workflows, "WATCH_WAKE_MS", 200)

    stop = asyncio.Event()
    watch = asyncio.create_task(workflows.local_watch(stop))
    await asyncio.sleep(0.5)  # the watcher is up
    (root / "Acme" / ".tumnis" / "state.json").write_text("{}")
    (root / "Acme" / "notes.md").write_text("# Notes\n")
    (root / "Acme" / "more.md").write_text("# More\n")
    await asyncio.wait_for(seen.wait(), WAIT_S)
    stop.set()
    await asyncio.wait_for(watch, WAIT_S)

    assert set(queued) == {("ws-1", "loc-a")}
    assert len(queued) <= 2  # one per settled batch of changes, never one per file


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-15")
async def test_no_watched_location_waits_for_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    async def roots() -> list[tuple[str, str, str]]:
        return []

    monkeypatch.setattr(sync, "watched_roots", roots)
    stop = asyncio.Event()
    watch = asyncio.create_task(workflows.local_watch(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(watch, WAIT_S)
    assert watch.done()
