"""`FakeGitHubStatus`: GitHub's read-only status API replayed from the recordings (P2-13).

Every recorded pull request (the unconditional reads of `tests/recordings/github/*.json`)
is loaded into plain dicts a test can script, keyed by lower-cased owner and repository:

- `pulls[(owner, repo, number)]` -> `PullView`
- `statuses[(owner, repo, sha)]` -> `list[StatusView]`
- `check_runs[(owner, repo, sha)]` -> `list[CheckRunView]`
- `reviews[(owner, repo, number)]` -> `list[ReviewView]`

A read of an unknown pull request or commit raises `AdapterRejected("404")`, as GitHub
answers. The ETag of a read is a hash of the value now in the dict, so changing a dict
changes the ETag, and a read that sends the current one answers Not Modified (counted in
`not_modified`). `calls` records every read as `(operation, key)`, the key being
`owner/repo#number` for a pull request and its reviews and the commit sha for statuses and
check runs. `pause()` holds every read until `resume()` (a workflow running on another
thread waits; the test's own loop does not).
"""

import asyncio
import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel

from tumnis.core.adapters.errors import AdapterRejected
from tumnis.core.adapters.registry import Health
from tumnis.modules.github.adapters.port import Fetched
from tumnis.modules.github.rules import CheckRunView, PullView, ReviewView, StatusView

NAME: Final = "github.status"
RECORDINGS: Final = Path(__file__).resolve().parents[1] / "tests" / "recordings" / "github"
PAUSE_LIMIT_S: Final = 60.0  # a forgotten resume() fails the test instead of hanging it

type Key = tuple[str, str, int | str]


class FakeGitHubStatus:
    def __init__(self) -> None:
        self.pulls: dict[Key, PullView] = {}
        self.statuses: dict[Key, list[StatusView]] = {}
        self.check_runs: dict[Key, list[CheckRunView]] = {}
        self.reviews: dict[Key, list[ReviewView]] = {}
        self.calls: list[tuple[str, str]] = []
        self.not_modified = 0
        self._running = threading.Event()
        self._running.set()
        self._load()

    # --- Scripting --------------------------------------------------------------------------

    def pause(self) -> None:
        """Hold every read until `resume()`."""
        self._running.clear()

    def resume(self) -> None:
        self._running.set()

    def _load(self) -> None:
        for path in sorted(RECORDINGS.glob("*.json")):
            rec = json.loads(path.read_text())
            owner, repo, number = rec["pull"]["owner"], rec["pull"]["repo"], rec["pull"]["number"]
            for ex in rec["exchanges"]:
                if ex["if_none_match"] is not None:
                    continue
                body, tail = ex["body"], ex["path"].rsplit("/", 1)[-1]
                if tail == "status":
                    sha = ex["path"].split("/")[-2]
                    self.statuses[owner, repo, sha] = [StatusView(**s) for s in body["statuses"]]
                elif tail == "check-runs":
                    sha = ex["path"].split("/")[-2]
                    self.check_runs[owner, repo, sha] = [
                        CheckRunView.model_validate(r) for r in body["check_runs"]
                    ]
                elif tail == "reviews":
                    self.reviews[owner, repo, number] = [ReviewView.model_validate(r) for r in body]
                else:
                    self.pulls[owner, repo, number] = PullView.model_validate(body)

    # --- The port ---------------------------------------------------------------------------

    async def get_pull(
        self, owner: str, repo: str, number: int, etag: str | None
    ) -> Fetched[PullView]:
        await self._gate()
        self.calls.append(("get_pull", _pull_key(owner, repo, number)))
        return self._answer("get_pull", self.pulls, (owner, repo, number), etag)

    async def get_combined_status(
        self, owner: str, repo: str, ref: str, etag: str | None
    ) -> Fetched[list[StatusView]]:
        await self._gate()
        self.calls.append(("get_combined_status", ref))
        return self._answer("get_combined_status", self.statuses, (owner, repo, ref), etag)

    async def list_check_runs(
        self, owner: str, repo: str, ref: str, etag: str | None
    ) -> Fetched[list[CheckRunView]]:
        await self._gate()
        self.calls.append(("list_check_runs", ref))
        return self._answer("list_check_runs", self.check_runs, (owner, repo, ref), etag)

    async def list_reviews(
        self, owner: str, repo: str, number: int, etag: str | None
    ) -> Fetched[list[ReviewView]]:
        await self._gate()
        self.calls.append(("list_reviews", _pull_key(owner, repo, number)))
        return self._answer("list_reviews", self.reviews, (owner, repo, number), etag)

    async def health(self) -> Health:
        return "ok"

    # --- Internals --------------------------------------------------------------------------

    async def _gate(self) -> None:
        if not self._running.is_set():
            await asyncio.to_thread(self._running.wait, PAUSE_LIMIT_S)

    def _answer[T](self, op: str, table: dict[Key, T], key: Key, etag: str | None) -> Fetched[T]:
        owner, repo, last = key
        found = table.get((owner.lower(), repo.lower(), last))
        if found is None:
            raise AdapterRejected(NAME, op, "404")
        current = _etag(found)
        if etag == current:
            self.not_modified += 1
            return Fetched(None, current)
        return Fetched(found, current)


def _pull_key(owner: str, repo: str, number: int) -> str:
    return f"{owner}/{repo}#{number}".lower()


def _etag(value: Any) -> str:
    """A weak ETag over the value's JSON: it changes whenever the value does."""
    items: Any = [value] if isinstance(value, BaseModel) else list(value)
    digest = hashlib.sha256(
        json.dumps([item.model_dump(mode="json") for item in items], sort_keys=True).encode()
    ).hexdigest()
    return f'W/"{digest}"'
