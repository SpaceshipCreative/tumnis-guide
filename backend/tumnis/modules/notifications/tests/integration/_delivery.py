"""Helpers for the Discord delivery tests (P2-16). No assertions: they arrange the push
world (`_push.PushWorld`) with the master profile on a connected fake runner, and read the
rows delivery leaves behind as the owner, so the code under test may change without
touching a locked test body.

- `DeliveryWorld` (the `delivery` fixture): a `PushWorld` (levels, tasks, moves, review
  items, focus events, settling) plus `runner` (the fake runner carrying the master
  `tumnis-master` and the project agent `acme-site-agent`), `master_id`, `project_agent()`
  (registers the project agent on the tasks' project), `master_says(message)` and
  `master_fails()` (the master's `focus` skill answers or fails), `retry_quickly(n)`
  (Discord delivery gives up after n attempts, a moment apart), `notify_runs()`,
  `runs_of(profile_id)`, `discord_attempts()`, `review_count()` and `dead_letters()`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from tumnis.modules.notifications.tests.integration._push import PushWorld, rows

if TYPE_CHECKING:
    import httpx

    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunner, FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

MASTER = "tumnis-master"
PROJECT_AGENT = "acme-site-agent"
FOCUS_SKILL = "focus"


class DeliveryWorld(PushWorld):
    """The push world with the master profile online on a fake runner."""

    def __init__(  # noqa: PLR0917  # the fixtures it stands on
        self,
        workspace: WorkspaceHandle,
        clock: FixedClock,
        db: DbUrls,
        sys_db: DbUrls,
        http: httpx.AsyncClient,
        fake: Any,
        runners: FakeRunnerFactory,
    ) -> None:
        super().__init__(workspace, clock, db, sys_db, http, fake)
        self.runners = runners
        self.runner: FakeRunner = runners(profiles=[MASTER, PROJECT_AGENT])
        self.master_id = runners.register_profile(MASTER, runner=self.runner, role="master")
        self.forget_setup()

    # --- arranging ---------------------------------------------------------------------

    async def project_agent(self) -> UUID:
        """The project agent profile of the tasks' project (made now if need be)."""
        if self._project is None:
            self._project = await self.project()
        made = self.runners.register_profile(
            PROJECT_AGENT, runner=self.runner, role="project", project_id=self._project
        )
        self.forget_setup()
        return made

    def master_says(self, message: str = "Time to start.") -> None:
        """The master's focus skill posts and answers `{"message": ...}` from now on."""
        self.runner.script(MASTER, FOCUS_SKILL, {"message": message})

    def retry_quickly(self, attempts: int) -> None:
        """Discord delivery gives up after `attempts` attempts, a moment apart."""
        from tumnis.modules.notifications import workflows  # noqa: PLC0415

        workflows.use_discord(max_attempts=attempts, base_delay_s=0.05)

    def master_fails(self) -> None:
        """The master's focus skill fails every run from now on (the gateway is down)."""
        self.runner.script(MASTER, FOCUS_SKILL, None, status="failed")

    # --- reading -----------------------------------------------------------------------

    def notify_runs(self) -> list[dict[str, Any]]:
        return rows(
            self.db,
            "SELECT * FROM runs WHERE workspace_id = %s AND kind = 'notify'"
            " ORDER BY created_at, id",
            self.workspace.id,
        )

    def runs_of(self, profile_id: UUID) -> list[dict[str, Any]]:
        return rows(
            self.db,
            "SELECT * FROM runs WHERE workspace_id = %s AND profile_id = %s"
            " ORDER BY created_at, id",
            self.workspace.id,
            profile_id,
        )

    def discord_attempts(self) -> list[dict[str, Any]]:
        return [a for a in self.attempts() if a["channel"] == "discord"]

    async def review_count(self) -> int:
        """The in-app review badge (`GET /v1/review/count`)."""
        answer = await self.http.get("/v1/review/count")
        answer.raise_for_status()
        return int(answer.json()["count"])

    async def dead_letters(self) -> list[dict[str, Any]]:
        """The open dead letters Settings lists (`GET /v1/dead-letters`)."""
        answer = await self.http.get("/v1/dead-letters")
        answer.raise_for_status()
        return list(answer.json()["items"])
