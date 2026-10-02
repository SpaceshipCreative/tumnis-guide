"""A run ends at the test clock's time in fakes mode (Scott decision 86, A1.6): with the
api's clock fixed (`POST /v1/test/clock` stores the instant in the fake-script store), the
worker stamps a finished run's `finished_at` with it, so the day summary counts the run on
the journey's day. Nothing fixed: the system clock, as in every real deployment."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.agents.tests.integration._enrich import (
    agent_project,
    enrichment_settings,
    new_task,
    recorded,
    relay,
    rows,
    task_row,
    until,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from dbos import DBOS

    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

ACME = "acme-site"
FIXED = datetime(2026, 3, 9, 12, 30, tzinfo=UTC)


@pytest.fixture
def stored_scripts() -> Iterator[None]:
    """The fake-script store on, as the worker enables it with fakes."""
    from tumnis.core import fake_scripts  # noqa: PLC0415

    fake_scripts.enable()
    try:
        yield
    finally:
        fake_scripts.disable()


async def _enriched_run_end(
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    fake_runner: FakeRunnerFactory,
) -> datetime:
    import tumnis.modules.agents.events  # noqa: F401, PLC0415  # registers the subscribers

    runner = fake_runner(profiles=[ACME])
    project_id = agent_project(fake_runner, runner, ACME, db)
    runner.script(ACME, "enrich", recorded("enrich_ok_human"))
    with enrichment_settings(clock):
        task = await new_task(workspace, clock, project_id, "Send the March invoice", label="human")
        await relay()
        assert await until(lambda: task_row(db, task.id)["enrichment_status"] == "done")
    [run] = rows(db, "SELECT kind, status, finished_at FROM runs")
    assert (run["kind"], run["status"]) == ("enrich", "succeeded")
    finished: datetime = run["finished_at"]
    return finished


@pytest.mark.req("REL-7")
@pytest.mark.wp("P1-18")
async def test_run_ends_at_the_fixed_test_clock(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    stored_scripts: None,
) -> None:
    from tumnis.core import fake_scripts  # noqa: PLC0415

    await fake_scripts.store_fixed_clock(FIXED)
    assert await _enriched_run_end(workspace, clock, db, fake_runner) == FIXED


@pytest.mark.req("REL-7")
@pytest.mark.wp("P1-18")
async def test_run_ends_at_the_system_clock_with_nothing_fixed(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    stored_scripts: None,
) -> None:
    finished = await _enriched_run_end(workspace, clock, db, fake_runner)
    assert abs(finished - datetime.now(UTC)) < timedelta(minutes=5)
