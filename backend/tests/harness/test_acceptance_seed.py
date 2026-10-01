"""The acceptance seed (Scott decision 37): the rows the phase 1 and phase 2 acceptance
suites name, in their own set.

The `seed` set stays as it is (three projects, thirty tasks, three Today tasks: locked by
T-P0-02-01, T-P0-23-10 and T-P0-17-20), so the acceptance rows live in
backend/fixtures/acceptance and load two ways:

- `SeedSet.acceptance` (`POST /v1/test/reset?set=acceptance`, the e2e journeys): the seed
  workspace and user, then the acceptance projects, tasks, briefs, calendar, runner and
  agents. No task is in Today, so A1.6's `Rolls over` holds only the plan's own tasks.
- `ACCEPTANCE_WORLD` (the backend acceptance `seed` fixture): the whole seed set plus the
  acceptance projects and briefs, without the runner and agents (`ACCEPTANCE_AGENTS`), which
  the acceptance `fake_runner` fixture makes in the seed workspace.
"""

from __future__ import annotations

import json
from datetime import date, time
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest

if TYPE_CHECKING:
    from tumnis.core.clock import FixedClock
    from tumnis.seed import InMemorySink, SeedResult

pytestmark = [
    pytest.mark.req("A1.4", "A1.6", "A2.1", "A2.6"),
    pytest.mark.wp("SEED"),
]

ANCHOR = date(2026, 3, 9)  # Monday, the journeys' day
TZ = ZoneInfo("America/New_York")
ACME, BETA, GAMMA = "Acme site", "Beta app", "Gamma ops"
PROFILE_SCOPES = {"tasks:read", "tasks:write", "context:read"}
MASTER_SCOPES = {"tasks:read", "tasks:write", "delegate"}
FOOTER_EMAIL = "https://mail.example.com/acme/threads/footer-link"

# title: (project, label, estimate in minutes, due offset in days from the anchor)
TASKS: dict[str, tuple[str, str, int | None, int | None]] = {
    "Invoice Acme for phase one": (ACME, "human", 60, None),
    "Send logo drafts to Acme": (ACME, "hybrid", 30, None),
    "Write Acme proposal": (ACME, "human", 90, 0),
    "Fix footer link": (ACME, "ai", None, None),
    "Write proposal": (BETA, "human", 50, None),
    "Record lesson one": (GAMMA, "human", 90, None),
    "Book a guest for lesson three": (GAMMA, "human", 30, None),
    "Generate March analytics report": (GAMMA, "ai", None, None),
}
# profile: (role, project, key scopes)
AGENTS: dict[str, tuple[str, str | None, set[str]]] = {
    "tumnis-master": ("master", None, MASTER_SCOPES),
    "acme-site": ("project", ACME, PROFILE_SCOPES),
    "beta-app": ("project", BETA, PROFILE_SCOPES),
    "gamma-ops": ("project", GAMMA, PROFILE_SCOPES),
}
# The busy events of each day, local wall-clock (A1.2's Monday; Tuesday and Wednesday keep
# 09:00 to 10:00 busy, so A2.6's 50-minute block is 10:00 to 10:50).
BUSY: dict[date, list[tuple[time, time]]] = {
    ANCHOR: [(time(9), time(10)), (time(12), time(13)), (time(15), time(15, 30))],
    date(2026, 3, 10): [(time(9), time(10))],
    date(2026, 3, 11): [(time(9), time(10))],
}
# The runner recordings the journeys script, and the plan picks they name by title.
PLAN_RECORDINGS = ("plan__monday_four_picks", "plan__ninety_first", "plan__write_proposal_block")


async def _acceptance(clock: FixedClock) -> tuple[InMemorySink, SeedResult]:
    from tumnis.seed import SEED_PATHS, InMemorySink, SeedSet, load_seed  # noqa: PLC0415

    sink = InMemorySink()
    result = await load_seed(SEED_PATHS[SeedSet("acceptance")], sink, anchor=ANCHOR, clock=clock)
    return sink, result


def _of(sink: InMemorySink, kind: str) -> list[Any]:
    return [stored for stored in sink.records if stored.kind == kind]


def _names(sink: InMemorySink) -> dict[Any, str]:
    """Project id -> project name."""
    return {p.id: p.rec.name for p in _of(sink, "project")}


async def test_acceptance_set_is_the_seed_user_and_three_projects_with_briefs(
    clock: FixedClock,
) -> None:
    """T-SEED-01
    The acceptance set signs in as the seed user (seed/workspace.yaml) and holds `Acme
    site`, `Beta app` and `Gamma ops`, each with a brief."""
    from tumnis.seed import SEED_PATHS, InMemorySink, SeedSet, load_seed  # noqa: PLC0415

    sink, result = await _acceptance(clock)
    seed = InMemorySink()
    await load_seed(SEED_PATHS[SeedSet.seed], seed, anchor=ANCHOR, clock=clock)

    assert [w.rec for w in _of(sink, "workspace")] == [w.rec for w in _of(seed, "workspace")]
    assert [u.rec for u in _of(sink, "user")] == [u.rec for u in _of(seed, "user")]
    assert [p.rec.name for p in _of(sink, "project")] == [ACME, BETA, GAMMA]
    names = _names(sink)
    briefs = {
        names[d.parents["project"]]: d.rec for d in _of(sink, "document") if d.rec.role == "brief"
    }
    assert set(briefs) == {ACME, BETA, GAMMA}
    assert all(brief.body.strip() for brief in briefs.values())
    assert set(result.ids) == {stored.rec.key for stored in sink.records}


async def test_acceptance_tasks_are_the_ones_the_journeys_name(clock: FixedClock) -> None:
    """T-SEED-02
    Every task the journeys and the plan recordings name, in its project, with its label,
    estimate, due date and a first action; none in Today (A1.6 rolls over only the plan's)."""
    sink, _ = await _acceptance(clock)
    names = _names(sink)
    tasks = {t.rec.title: t for t in _of(sink, "task")}

    assert set(tasks) == set(TASKS)
    for title, (project, label, minutes, due) in TASKS.items():
        task = tasks[title]
        assert names[task.parents["project"]] == project, title
        assert task.rec.label == label, title
        assert task.rec.estimate_minutes == minutes, title
        expected_due = None if due is None else date.fromordinal(ANCHOR.toordinal() + due)
        assert task.rec.due_on == expected_due, title
        assert task.rec.first_action, title
        assert task.parents["parent"] is None, title
    assert {t.rec.status for t in tasks.values()} == {"backlog"}


async def test_fix_footer_link_has_criteria_and_one_tainted_email(clock: FixedClock) -> None:
    """T-SEED-03
    A2.1's `Fix footer link`: an AI task with a first action, acceptance criteria and one
    linked email (a bare URL target: outside content, so tainted)."""
    sink, _ = await _acceptance(clock)
    footer = next(t for t in _of(sink, "task") if t.rec.title == "Fix footer link")
    links = _of(sink, "link")

    assert footer.rec.acceptance_criteria
    assert [(link.parents["task"], link.rec.url) for link in links] == [(footer.id, FOOTER_EMAIL)]


async def test_acceptance_runner_and_agents(clock: FixedClock) -> None:
    """T-SEED-04
    One runner `homelab-hermes`; the master `tumnis-master` and the project agents
    `acme-site`, `beta-app` and `gamma-ops` on it, each with the scopes of the key its runs
    issue task tokens from (the key itself is generated when the seed loads)."""
    sink, _ = await _acceptance(clock)
    names = _names(sink)
    [runner] = _of(sink, "runner")

    assert runner.rec.name == "homelab-hermes"
    agents = {a.rec.name: a for a in _of(sink, "agent")}
    assert set(agents) == set(AGENTS)
    for name, (role, project, scopes) in AGENTS.items():
        agent = agents[name]
        assert agent.rec.role == role, name
        assert names.get(agent.parents["project"]) == project, name
        assert agent.parents["runner"] == runner.id, name
        assert set(agent.rec.key_scopes) == scopes, name


async def test_acceptance_calendar_days(clock: FixedClock) -> None:
    """T-SEED-05
    Monday's busy events are A1.2's; Tuesday and Wednesday are busy 09:00 to 10:00."""
    sink, _ = await _acceptance(clock)
    busy: dict[date, list[tuple[time, time]]] = {}
    for event in _of(sink, "event"):
        start, end = event.rec.start_at.astimezone(TZ), event.rec.end_at.astimezone(TZ)
        busy.setdefault(start.date(), []).append((start.time(), end.time()))

    assert {day: sorted(spans) for day, spans in busy.items()} == BUSY


async def test_plan_recordings_name_acceptance_tasks() -> None:
    """T-SEED-06
    Each `title:` pick and alternate of the recordings the journeys script names an
    acceptance task, and A1.1's enrichment recording answers the agent's first action and
    20 minutes."""
    from tests.acceptance._phase1 import runner_result  # noqa: PLC0415

    for name in PLAN_RECORDINGS:
        reply = runner_result(name)
        named = [p["task_id"] for p in reply["picks"]] + list(reply.get("alternates") or [])
        assert named, name
        for task_id in named:
            assert task_id.startswith("title:"), (name, task_id)
            assert task_id.removeprefix("title:") in TASKS, (name, task_id)
    assert [p["task_id"] for p in runner_result("plan__write_proposal_block")["picks"]] == [
        "title:Write proposal"
    ]
    enrich = runner_result("enrich__hybrid_invoice")
    assert enrich["first_action"] == "Open last month's invoice in Wave and duplicate it"
    assert enrich["estimate_minutes"] == 20
    assert enrich["acceptance_criteria"]
    assert enrich["task_id"] == "00000000-0000-0000-0000-000000000000"  # the run's own task


async def test_backend_acceptance_world_is_the_seed_plus_the_projects(clock: FixedClock) -> None:
    """T-SEED-07
    The backend acceptance `seed`: the seed set unchanged, then the three acceptance
    projects with their tasks and briefs; no runner or agent (the archive round trip makes
    `homelab-hermes` and `acme-site` itself; the acceptance `fake_runner` makes them for
    A1.4), and the agents file holds exactly the runner and the four agents."""
    from tumnis.seed import (  # noqa: PLC0415
        ACCEPTANCE_AGENTS,
        ACCEPTANCE_WORLD,
        SEED_PATHS,
        InMemorySink,
        SeedSet,
        load_seed,
        read_seed,
    )

    seed, world = InMemorySink(), InMemorySink()
    await load_seed(SEED_PATHS[SeedSet.seed], seed, anchor=ANCHOR, clock=clock)
    await load_seed(ACCEPTANCE_WORLD, world, anchor=ANCHOR, clock=clock)

    def keys(sink: InMemorySink, kind: str) -> list[str]:
        return [stored.rec.key for stored in sink.records if stored.kind == kind]

    assert keys(world, "workspace") == keys(seed, "workspace")
    projects = [p.rec.name for p in _of(world, "project")]
    assert projects[:3] == [p.rec.name for p in _of(seed, "project")]
    assert projects[3:] == [ACME, BETA, GAMMA]
    assert keys(world, "task")[: len(keys(seed, "task"))] == keys(seed, "task")
    assert {t.rec.title for t in _of(world, "task")} >= set(TASKS)
    assert len(keys(world, "event")) == len(keys(seed, "event"))  # the seed's day only
    assert _of(world, "runner") == []
    assert _of(world, "agent") == []

    docs = read_seed(ACCEPTANCE_AGENTS)
    assert [r.name for doc in docs for r in doc.runners] == ["homelab-hermes"]
    assert sorted(a.name for doc in docs for a in doc.agents) == sorted(AGENTS)


async def test_seed_set_counts_are_unchanged(clock: FixedClock) -> None:
    """T-SEED-08
    New record kinds count only when a set holds them: the seed set's counts keep the
    six kinds T-P0-02-01 names."""
    from tumnis.seed import SEED_PATHS, InMemorySink, SeedSet, load_seed  # noqa: PLC0415

    seed = await load_seed(SEED_PATHS[SeedSet.seed], InMemorySink(), anchor=ANCHOR, clock=clock)
    _, acceptance = await _acceptance(clock)

    assert set(seed.counts) == {"workspace", "user", "project", "task", "event", "document"}
    assert acceptance.counts["runner"] == 1
    assert acceptance.counts["agent"] == len(AGENTS)
    assert acceptance.counts["link"] == 1


def test_recordings_are_valid_json() -> None:
    """T-SEED-09
    The two new recordings parse (a stray comma would only show in a journey)."""
    from tests.acceptance._phase1 import RUNNER_RECORDINGS  # noqa: PLC0415

    for name in ("enrich__hybrid_invoice", "plan__write_proposal_block"):
        json.loads((RUNNER_RECORDINGS / f"{name}.result.json").read_text())
