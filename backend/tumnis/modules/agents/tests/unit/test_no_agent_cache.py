"""The enrichment subscribers' memory of projects without a provisioned agent (P1-08,
review of PR #109): the relay runs them for every task write, so a project seen without
an agent is asked about once per `NO_AGENT_TTL`, not once per write."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.core.clock import FixedClock
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.types import SYSTEM_ACTOR

if TYPE_CHECKING:
    from collections.abc import Iterator

T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


class _Answers:
    """`workflows.agent_provisioned`, scripted per project, counting the questions."""

    def __init__(self) -> None:
        self.provisioned: set[uuid.UUID] = set()
        self.asked: list[uuid.UUID] = []

    async def __call__(self, project_id: uuid.UUID, *, ctx: Any) -> bool:
        self.asked.append(project_id)
        return project_id in self.provisioned


@pytest.fixture
def clock() -> Iterator[FixedClock]:
    from tumnis.modules.agents import api  # noqa: PLC0415

    fixed = FixedClock(T0)
    api.configure_enrichment(clock=fixed)
    try:
        yield fixed
    finally:
        api.configure_enrichment()


@pytest.fixture
def answers(monkeypatch: pytest.MonkeyPatch) -> _Answers:
    from tumnis.modules.agents import events, workflows  # noqa: PLC0415

    scripted = _Answers()
    monkeypatch.setattr(workflows, "agent_provisioned", scripted)
    monkeypatch.setattr(events, "_no_agent", {})
    return scripted


CTX = WorkspaceContext(uuid.uuid4(), SYSTEM_ACTOR)


@pytest.mark.req("FR-4.4")
@pytest.mark.wp("P1-08")
async def test_a_project_without_an_agent_is_asked_once_per_ttl(
    clock: FixedClock, answers: _Answers
) -> None:
    from tumnis.modules.agents.events import NO_AGENT_TTL, project_has_agent  # noqa: PLC0415

    project = uuid.uuid4()
    assert not await project_has_agent(project, CTX)
    clock.set(T0 + NO_AGENT_TTL - timedelta(milliseconds=1))
    assert not await project_has_agent(project, CTX)
    assert answers.asked == [project]

    answers.provisioned.add(project)
    clock.set(T0 + NO_AGENT_TTL)
    assert await project_has_agent(project, CTX)
    assert await project_has_agent(project, CTX)
    assert answers.asked == [project, project, project]  # a provisioned one is never kept


@pytest.mark.req("FR-4.4")
@pytest.mark.wp("P1-08")
async def test_each_project_is_remembered_on_its_own(clock: FixedClock, answers: _Answers) -> None:
    from tumnis.modules.agents.events import project_has_agent  # noqa: PLC0415

    without, with_agent = uuid.uuid4(), uuid.uuid4()
    answers.provisioned.add(with_agent)
    assert not await project_has_agent(without, CTX)
    assert await project_has_agent(with_agent, CTX)
    assert not await project_has_agent(without, CTX)
    assert answers.asked == [without, with_agent]


@pytest.mark.req("FR-4.4")
@pytest.mark.wp("P1-08")
async def test_the_memory_is_bounded(
    clock: FixedClock, answers: _Answers, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tumnis.modules.agents import events  # noqa: PLC0415

    monkeypatch.setattr(events, "NO_AGENT_MAX", 3)
    projects = [uuid.uuid4() for _ in range(5)]
    for project in projects:
        assert not await events.project_has_agent(project, CTX)
    assert list(events._no_agent) == projects[2:]
