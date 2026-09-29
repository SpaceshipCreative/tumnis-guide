"""Coolify's HTTP answers replayed from the recordings (P2-14): the real read-only client
runs against them in the contract layer, with no socket opened.

`Replay(recording)` is an `httpx.MockTransport` handler answering, by path:
- `GET /api/v1/applications/<uuid>`: the recorded application;
- `GET /api/v1/deployments/applications/<uuid>`: the recorded `{count, deployments}`,
  the list cut to `take` (recorded newest first, as Coolify answers);
- anything else: Coolify's 404 `{"message": "Application not found"}`.
Every request is kept in `requests` for assertions.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from tumnis.core.clock import FixedClock
from tumnis.core.net import NetPolicy

if TYPE_CHECKING:
    from tumnis.modules.coolify.adapters.coolify_status import CoolifyStatusApi

RECORDINGS = Path(__file__).resolve().parent / "recordings" / "coolify"
BASE_URL = "https://coolify.example.com"
TOKEN = "fake-read-token"  # an invented value
COOLIFY_ADDRESS = "192.168.50.10"  # the homelab LAN: self-hosted mode lets it through
POLICY = NetPolicy(mode="self-hosted")
T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


def recording_names() -> list[str]:
    return sorted(path.stem for path in RECORDINGS.glob("*.json"))


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((RECORDINGS / f"{name}.json").read_text())
    return data


class Replay:
    def __init__(self, *recordings: dict[str, Any]) -> None:
        self.requests: list[httpx.Request] = []
        self._apps = {rec["app_uuid"]: rec["responses"] for rec in recordings}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        for prefix, part in (
            ("/api/v1/deployments/applications/", "deployments"),
            ("/api/v1/applications/", "application"),
        ):
            if path.startswith(prefix):
                responses = self._apps.get(path.removeprefix(prefix))
                if responses is not None:
                    return httpx.Response(200, json=_answer(responses, part, request))
        return httpx.Response(404, json={"message": "Application not found"})


def _answer(responses: dict[str, Any], part: str, request: httpx.Request) -> Any:
    """The recorded answer; a deployments list is cut to `take` as Coolify does."""
    body = responses[part]
    if part != "deployments":
        return body
    take = int(request.url.params.get("take", "10"))
    return {**body, "deployments": body["deployments"][:take]}


async def _coolify_address(host: str, port: int) -> list[str]:
    return [COOLIFY_ADDRESS]


def recorded_api(replay: Replay) -> "CoolifyStatusApi":
    """The real Coolify client whose HTTP goes to the replay."""
    from tumnis.modules.coolify.adapters.coolify_status import CoolifyStatusApi  # noqa: PLC0415

    return CoolifyStatusApi(
        base_url=BASE_URL,
        token=TOKEN,
        policy=POLICY,
        resolver=_coolify_address,
        transport=replay.transport(),
        clock=FixedClock(T0),
    )
