"""The working directory of a skill case run (P2-12): the files it holds, and the files
the run removed from it.

A removal is noticed twice. The full Tumnis mock checks before it answers each call
(`DeletionWatch`, reading the state file the harness writes before each attempt), so a
deletion lands in the timeline before the first Tumnis call made after it, and the gated
rule sees whether it came before or after the approval. Whatever is still missing after
the run and was not seen yet is recorded at the end (`deletion_record`).
"""

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Final
from uuid import uuid4

from harness.mock_mcp_min import RecordedCall

HARNESS: Final = "harness"  # what the harness itself records: suite results, deletions
DELETE_TOOL: Final = "delete_files"
SKIP_DIRS: Final = frozenset({".git", ".tumnis", "__pycache__"})


def files_of(workdir: Path) -> set[str]:
    """Every file under `workdir` (relative, POSIX), outside .git and the query folder."""
    return {
        path.relative_to(workdir).as_posix()
        for path in workdir.rglob("*")
        if path.is_file() and not SKIP_DIRS & set(path.relative_to(workdir).parts)
    }


def write_state(path: Path, workdir: Path | None, files: Iterable[str] = ()) -> None:
    """What the mock's DeletionWatch reads for one attempt: the working directory and the
    files it held when the attempt started (no working directory: nothing to watch)."""
    state = {
        "attempt": uuid4().hex,
        "workdir": None if workdir is None else str(workdir),
        "files": sorted(files),
    }
    path.write_text(json.dumps(state) + "\n", encoding="utf-8")


class DeletionWatch:
    """The files gone from the attempt's working directory since the last check."""

    def __init__(self, state: Path | None) -> None:
        self.state = state
        self._attempt: str | None = None
        self._seen: set[str] = set()

    def check(self) -> list[str]:
        if self.state is None or not self.state.is_file():
            return []
        try:
            state = json.loads(self.state.read_text(encoding="utf-8"))
        except ValueError:
            return []
        if state.get("attempt") != self._attempt:
            self._attempt, self._seen = state.get("attempt"), set()
        if not state.get("workdir"):
            return []
        workdir = Path(state["workdir"])
        gone = sorted(set(state.get("files") or ()) - files_of(workdir) - self._seen)
        self._seen.update(gone)
        return gone


def recorded_deletions(timeline: Sequence[RecordedCall]) -> set[str]:
    """The paths the timeline already records as removed."""
    return {
        str(path)
        for call in timeline
        if (call.server, call.tool) == (HARNESS, DELETE_TOOL)
        for path in call.arguments.get("paths") or ()
    }


def deletion_record(
    before: set[str], workdir: Path, seen: Iterable[str] = ()
) -> list[RecordedCall]:
    """The files the run removed (deleted, or moved out of the working directory) and not
    `seen` yet, as one `harness.delete_files` call; [] when there are none."""
    gone = sorted(before - files_of(workdir) - set(seen))
    if not gone:
        return []
    return [RecordedCall(HARNESS, DELETE_TOOL, {"paths": gone}, None)]
