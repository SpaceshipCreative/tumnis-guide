"""The profile version on every run (P2-12, Quality rule 3, R-25): the daemon reports the
profile's `VERSION` in its protocol-2 `status{state: started}` message, `runs.profile_version`
keeps it, and the run view (`GET /v1/runs/{id}`) shows it, so a profile change is traceable
to the runs it made (PRD risk "Project profiles drift")."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.agents.tests.integration._runs import (
    T,
    finish,
    owner_rows,
    relay,
    run_world,
    wait_until,
)

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunner, FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]


def _started(runner: FakeRunner, run_id: uuid.UUID, profile: str, version: str) -> uuid.UUID:
    """The daemon's `status{state: started}` for the run, with the profile's VERSION."""
    frame: dict[str, Any] = {
        "schema_version": 1,
        "message_id": str(uuid.uuid4()),
        "correlation_id": f"run:{run_id}",
        "sent_at": T,
        "type": "status",
        "run_id": str(run_id),
        "state": "started",
        "profile": profile,
        "profile_version": version,
        "detail": None,
    }
    runner.send_raw(json.dumps(frame))
    return uuid.UUID(frame["message_id"])


def _wait_for_run(runner: FakeRunner, run_id: uuid.UUID) -> None:
    runner.wait_for(lambda r: any(m.run_id == run_id for m in r.runs()))


def _wait_for_ack(runner: FakeRunner, message_id: uuid.UUID) -> None:
    runner.wait_for(lambda r: message_id in r.acked)


def _succeeded(db: DbUrls, run_id: uuid.UUID) -> Callable[[], bool]:
    def check() -> bool:
        rows = owner_rows(db, "SELECT status FROM runs WHERE id = %s", (run_id,))
        return rows == [("succeeded",)]

    return check


@pytest.mark.req("Quality rule 3")
@pytest.mark.wp("P2-12")
async def test_profile_version_logged_with_every_run(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-12-10
    Two runs, the first started by a daemon reporting profile 1.1.0 and the second after
    the profile moved to 1.2.0: each run's `runs.profile_version` is the
    version its own `status{started}` carried, and `GET /v1/runs/{id}` shows it as
    `profile_version`, also after the run has finished. A run whose daemon sent no status
    shows `profile_version: null`.
    """
    world = await run_world(fake_runner, workspace, clock)
    seen: dict[uuid.UUID, str | None] = {}
    async with relay(db):
        for version in ("1.1.0", "1.2.0", None):
            task = await world.ai_task(f"Fix footer link ({version})")
            run_id = await world.request(task.id)
            _wait_for_run(world.runner, run_id)
            if version is not None:
                _wait_for_ack(world.runner, _started(world.runner, run_id, world.profile, version))
            finish(world.runner, run_id)
            assert await wait_until(_succeeded(db, run_id))
            seen[run_id] = version

    for run_id, version in seen.items():
        stored = owner_rows(db, "SELECT profile_version FROM runs WHERE id = %s", (run_id,))
        assert stored == [(version,)]
        shown = await session_client.get(f"/v1/runs/{run_id}")
        assert shown.status_code == 200, shown.text
        assert shown.json()["profile_version"] == version
