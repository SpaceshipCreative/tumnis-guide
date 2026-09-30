"""Provisioning edge cases found in review (P1-06, FR-2.1, FR-5.10): a link goes to the
runner that has the profile, and two projects whose names normalise alike both get a
profile."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.agents.tests.integration.test_provision import (
    MASTER,
    _create_project,
    _failed_items,
    _owner,
    _profile,
    _provisions,
    _settle,
    _status_is,
    _subscribers,
)

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P1-06")
async def test_link_goes_to_the_runner_that_has_the_profile(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """The master runs on `homelab-hermes`; `old-site` lives only on `laptop-hermes`.
    Linking to it asks `laptop-hermes`, not the master's runner, and ends `ready` there."""
    _subscribers()
    master = fake_runner(profiles=[MASTER])
    fake_runner.register_profile(MASTER, runner=master, role="master")
    laptop = fake_runner(profiles=["old-site"], name="laptop-hermes")

    project_id = await _create_project(workspace, clock, "Old Site", link="old-site")

    await _settle(_status_is(db, project_id, "ready"))
    [(runner_id,)] = _owner(
        db, "SELECT runner_id FROM agent_profiles WHERE project_id = %s", (project_id,)
    )
    assert runner_id == laptop.runner_id
    await asyncio.to_thread(laptop.wait_for, lambda r: len(_provisions(r)) == 1)
    assert _provisions(master) == []
    assert _failed_items(db, project_id) == []


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P1-06")
async def test_projects_named_alike_both_get_a_profile(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """Two projects whose names normalise to the same profile name, provisioned at the same
    time on different partitions: both end `ready`, under different names."""
    _subscribers()
    fake_runner(profiles=[MASTER])
    first = await _create_project(workspace, clock, "Acme Site", relay=False)
    second = await _create_project(workspace, clock, "Acme  Site!", relay=False)
    from tumnis.core.events import relay_once  # noqa: PLC0415

    assert await relay_once() >= 2

    await _settle(_status_is(db, first, "ready"))
    await _settle(_status_is(db, second, "ready"))
    names = {_profile(db, first)[0][0], _profile(db, second)[0][0]}
    assert len(names) == 2
    assert "acme-site" in names
