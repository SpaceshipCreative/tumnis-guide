"""The acceptance suites' own fixtures.

- `seed`: the seed set plus the acceptance projects the phase 1 suite names (`Acme site`,
  `Beta app`, `Gamma ops`, with their tasks and briefs: `tumnis.seed.ACCEPTANCE_WORLD`,
  Scott decision 37). It loads only once the master key and the pepper are in place (the
  seed user's password and TOTP secret are stored with them), whatever order a test names
  its fixtures in, so the seed user can sign in to the `app` fixture's app.
- `fake_runner`: for a test that also uses `seed`, the factory works in the seed workspace
  and has the acceptance runner `homelab-hermes` connected with the agents of
  `tumnis.seed.ACCEPTANCE_AGENTS` (`tumnis-master`, `acme-site`, `beta-app`, `gamma-ops`)
  registered on it; `fake_runner.offline(profile)` and `fake_runner.script(profile, skill,
  ...)` act on that runner (A1.4). Other tests get the `workspace` fixture's factory.
- No test leaves decision fakes behind for the next (`_phase1.script_label` and
  `fail_decision_providers` set them process-wide, P1-07).
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import partial
from typing import TYPE_CHECKING, Any

import pytest

from tests._labels import reset_label_fakes

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunner, FakeRunnerFactory
    from tests.fixtures import MasterKeyFile, PepperFile, WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.seed import SeedResult


@pytest.fixture
async def seed(
    db: DbUrls, clock: FixedClock, master_key_file: MasterKeyFile, pepper_file: PepperFile
) -> SeedResult:
    from tests.fixtures import _load_set  # noqa: PLC0415
    from tumnis.seed import ACCEPTANCE_WORLD  # noqa: PLC0415

    del master_key_file, pepper_file  # requested for their order only
    return await _load_set(ACCEPTANCE_WORLD, db, clock)


@pytest.fixture
def fake_runner(
    request: pytest.FixtureRequest,
    fake_runner: FakeRunnerFactory,
    app: FastAPI,
    db: DbUrls,
    clock: FixedClock,
) -> Iterator[Any]:
    """The `fake_runner` factory; with `seed`, the seed workspace's (see the module doc)."""
    if "seed" not in request.fixturenames:
        yield fake_runner
        return
    seed: SeedResult = request.getfixturevalue("seed")
    factory = _seed_factory(fake_runner, seed, db, clock)
    try:
        yield factory
    finally:
        factory.disconnect_all()


def _seed_factory(
    base: FakeRunnerFactory, seed: SeedResult, db: DbUrls, clock: FixedClock
) -> _SeedFactory:
    """A factory in the seed workspace with the acceptance runner connected. The seed's own
    outbox rows are marked sent first: they describe how the seed was written, not what a
    test does (relayed, `project.created` would provision a profile for every seed project
    on this runner, and `task.created` enrich all its tasks)."""
    from tests._auth import run_async  # noqa: PLC0415
    from tests.fixtures import WorkspaceHandle  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.seed import ACCEPTANCE_AGENTS, AgentSeed, read_seed  # noqa: PLC0415

    workspace_id, user_id = seed.ids["ws_main"], seed.ids["u_scott"]
    handle = WorkspaceHandle(
        id=workspace_id,
        name="Scott's workspace",
        ctx=WorkspaceContext(workspace_id, ActorRef(f"user:{user_id}")),
        user_id=user_id,
    )
    docs = read_seed(ACCEPTANCE_AGENTS)
    [runner_seed] = [runner for doc in docs for runner in doc.runners]
    agent_seeds = [agent for doc in docs for agent in doc.agents]
    factory = _SeedFactory(base.client, handle, clock)
    runner = factory(
        profiles=[agent.name for agent in agent_seeds], name=runner_seed.name, connect=False
    )
    for agent in agent_seeds:
        rec = AgentSeed(
            key=agent.key, name=agent.name, role=agent.role, key_scopes=tuple(agent.key_scopes)
        )
        project = None if agent.project is None else seed.ids[agent.project]
        run_async(partial(agents.seed_agent, workspace_id, runner.runner_id, project, rec))
    _mark_outbox_sent(db)
    runner.connect()
    factory.runner = runner
    return factory


class _SeedFactory:
    """The seed workspace's factory: the base factory's calls, plus `script` on its runner."""

    def __init__(self, client: Any, workspace: WorkspaceHandle, clock: FixedClock) -> None:
        from tests.fakes.fake_runner import _Factory  # noqa: PLC0415

        self._factory = _Factory(client, workspace, clock)
        self.client = client
        self.runner: FakeRunner | None = None

    def __call__(self, *args: Any, **kwargs: Any) -> FakeRunner:
        return self._factory(*args, **kwargs)

    def register_profile(self, name: str, **kwargs: Any) -> Any:
        return self._factory.register_profile(name, **kwargs)

    def offline(self, profile: str) -> None:
        self._factory.offline(profile)

    def script(self, profile: str, skill: str, output: Any, **kwargs: Any) -> None:
        """What the acceptance runner answers to `run` for (profile, skill)."""
        if self.runner is None:
            raise RuntimeError("the acceptance runner is not connected")
        self.runner.script(profile, skill, output, **kwargs)

    def disconnect_all(self) -> None:
        for made in self._factory.made:
            made.disconnect()


def _mark_outbox_sent(db: DbUrls) -> None:
    import psycopg  # noqa: PLC0415

    from tests._pg import OWNER  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER)) as conn:
        conn.execute(b"UPDATE outbox SET sent_at = now() WHERE sent_at IS NULL")


@pytest.fixture(autouse=True)
def _reset_label_fakes() -> Iterator[None]:
    yield
    reset_label_fakes()


@pytest.fixture(autouse=True)
def _reset_human_waits() -> Iterator[None]:
    """A2.2 and A2.3 shorten the long poll for the process (P2-05); put it back."""
    yield
    from tumnis.modules.agents import api as agents  # noqa: PLC0415

    agents.configure_human_waits()
