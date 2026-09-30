"""GitHub's HTTP answers replayed from the recordings (P2-13): the real read-only client
runs against them in the contract layer, with no socket opened. No assertions live here.

A recording (`recordings/github/<case>.json`) holds the pull request it is about
(`pull`: owner, repo, number), the recorded `exchanges` (method, path, query,
`if_none_match`, status, headers, body) in the order the client makes them, how many
rounds of reads it covers (`rounds`: 2 when the second round is answered 304 Not Modified)
and the `expected` artifact.

`Replay(*recordings)` is an `httpx.MockTransport` handler: a GET whose path, query and
`If-None-Match` match a recorded exchange gets its recorded answer; anything else gets
GitHub's 404 `{"message": "Not Found"}`. Every request is kept in `requests`.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from tumnis.core.clock import FixedClock

if TYPE_CHECKING:
    from tumnis.modules.github.adapters.github_status import GitHubStatusApi

RECORDINGS = Path(__file__).resolve().parent / "recordings" / "github"
TOKEN = "fake-read-token"  # an invented value
GITHUB_ADDRESS = "140.82.121.6"  # a public address: the hosted policy lets it through
T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


def recording_names() -> list[str]:
    return sorted(path.stem for path in RECORDINGS.glob("*.json"))


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((RECORDINGS / f"{name}.json").read_text())
    return data


def first_round(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """The exchanges answered 200 (the unconditional reads)."""
    return [ex for ex in rec["exchanges"] if ex["if_none_match"] is None]


class Replay:
    def __init__(self, *recordings: dict[str, Any]) -> None:
        self.requests: list[httpx.Request] = []
        self._exchanges = [ex for rec in recordings for ex in rec["exchanges"]]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for ex in self._exchanges:
            if (
                ex["method"] == request.method
                and ex["path"] == request.url.path
                and ex["query"] == dict(request.url.params)
                and ex["if_none_match"] == request.headers.get("if-none-match")
            ):
                return httpx.Response(
                    ex["status"],
                    headers=ex["headers"],
                    content=b"" if ex["body"] is None else json.dumps(ex["body"]).encode(),
                )
        return httpx.Response(
            404,
            json={
                "message": "Not Found",
                "documentation_url": "https://docs.github.com/rest",
                "status": "404",
            },
        )


async def _github_address(host: str, port: int) -> list[str]:
    return [GITHUB_ADDRESS]


def recorded_api(replay: Replay) -> "GitHubStatusApi":
    """The real GitHub client whose HTTP goes to the replay."""
    from tumnis.modules.github.adapters.github_status import GitHubStatusApi  # noqa: PLC0415

    return GitHubStatusApi(
        token=TOKEN,
        resolver=_github_address,
        transport=replay.transport(),
        clock=FixedClock(T0),
    )
