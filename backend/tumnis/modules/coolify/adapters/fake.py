"""`FakeCoolifyStatus`: Coolify replayed from the recordings (P2-14).

Every recording in `tests/recordings/coolify/*.json` is one application with its
deployments, keyed by the application UUID; an unknown UUID is rejected as Coolify answers
it (404). The fake's public surface is the port's three reads (T-P2-14-02), so scripting
is through plain attributes, not methods: `applications` and `deployments` (edit them to
script answers), `unavailable` (UUIDs whose reads raise `AdapterUnavailable`) and `calls`
((op, app_uuid) per read).
"""

import json
from pathlib import Path
from typing import Any, Final

from tumnis.core.adapters.errors import AdapterRejected, AdapterUnavailable
from tumnis.core.adapters.registry import Health
from tumnis.modules.coolify.adapters.port import TAKE_DEFAULT
from tumnis.modules.coolify.rules import ApplicationView, DeploymentView

NAME: Final = "coolify.status"
RECORDINGS: Final = Path(__file__).resolve().parents[1] / "tests" / "recordings" / "coolify"


class FakeCoolifyStatus:
    def __init__(self, **_deps: Any) -> None:
        """Takes and ignores the real adapter's arguments (base URL, token, policy)."""
        self.applications: dict[str, ApplicationView] = {}
        self.deployments: dict[str, list[DeploymentView]] = {}
        self.unavailable: set[str] = set()
        self.calls: list[tuple[str, str]] = []
        for path in sorted(RECORDINGS.glob("*.json")):
            responses = json.loads(path.read_text())["responses"]
            app = ApplicationView.model_validate(responses["application"])
            self.applications[app.uuid] = app
            self.deployments[app.uuid] = [
                DeploymentView.model_validate(item)
                for item in responses["deployments"]["deployments"]
            ]

    async def get_application(self, app_uuid: str) -> ApplicationView:
        self._read("get_application", app_uuid)
        return self.applications[app_uuid]

    async def list_deployments(
        self, app_uuid: str, take: int = TAKE_DEFAULT
    ) -> list[DeploymentView]:
        self._read("list_deployments", app_uuid)
        deps = self.deployments.get(app_uuid, [])
        return sorted(deps, key=lambda dep: dep.created_at, reverse=True)[:take]

    async def health(self) -> Health:
        return "degraded" if self.unavailable else "ok"

    def _read(self, op: str, app_uuid: str) -> None:
        self.calls.append((op, app_uuid))
        if app_uuid in self.unavailable:
            raise AdapterUnavailable(NAME, op, "503")
        if app_uuid not in self.applications:
            raise AdapterRejected(NAME, op, "404")
