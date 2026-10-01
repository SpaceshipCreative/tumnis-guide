"""Next-task preparation at Guardrail (P4-01, FR-10.6): when the current task starts, the next
one in today's plan that has no first action gets its project agent's enrichment run
ahead of time, so starting it costs nothing.

Day: Tuesday 2026-03-10 in New York (the `workspace` fixture), plan from 09:00.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from tumnis.modules.focus.tests.integration._focus import execute, rows, until
from tumnis.modules.focus.tests.integration._guardrail import (
    guardrail_day,
    task_row,
    workflows_named,
)

if TYPE_CHECKING:
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tumnis.core.clock import FixedClock
    from tumnis.modules.focus.tests.integration._focus import Focus

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

ACME = "acme-site"
RESULT = Path(__file__).resolve().parents[5] / "tests/fakes/recordings/runner"


@contextlib.contextmanager
def _enrichment(clock: FixedClock) -> Iterator[None]:
    """The enrichment's clock and short waits (R-30), and a Generation fake for its
    placeholder; restored afterwards."""
    from tumnis.core.adapters.registry import resolve  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.modules.decisions import api as decisions  # noqa: PLC0415
    from tumnis.settings import GenerationSettings  # noqa: PLC0415

    generation = resolve("decisions.vllm_generation", "fake")
    generation.script("Open the pull request")
    decisions.configure_generation(GenerationSettings(), provider=generation)
    agents.configure_enrichment(clock=clock, label_wait_s=0.0, run_timeout_s=20)
    try:
        yield
    finally:
        agents.configure_enrichment()
        decisions.configure_generation(GenerationSettings())


def _agent_project(fake_runner: FakeRunnerFactory, focus: Focus) -> tuple[UUID, Any]:
    """A project whose agent lives on a connected fake runner, provisioned (`ready`)."""
    runner = fake_runner(profiles=[ACME])
    profile_id = fake_runner.register_profile(ACME, runner=runner)
    execute(focus.db, "UPDATE agent_profiles SET status = 'ready' WHERE id = %s", profile_id)
    [row] = rows(focus.db, "SELECT project_id FROM agent_profiles WHERE id = %s", profile_id)
    focus.forget_setup()
    return row["project_id"], runner


@pytest.mark.req("FR-10.6")
@pytest.mark.wp("P4-01")
@pytest.mark.xfail(strict=True, reason="spec:P4-01")
async def test_next_task_enriched_ahead(
    dbos: Any, focus: Focus, fake_runner: FakeRunnerFactory
) -> None:
    """T-P4-01-07
    At Guardrail, A (with a first action) starts: B, next in today's plan and without a
    first action, gets an enrichment run (`enrich_task`) and its first action from the
    project agent; C, after B, is not prepared, and neither is A.
    """
    project, runner = _agent_project(fake_runner, focus)
    result = json.loads((RESULT / "enrich_ok_human.result.json").read_text())
    runner.script(ACME, "enrich", result)

    with _enrichment(focus.clock):
        day = await guardrail_day(
            focus, project=project, first_actions=("Open the invoice template", None, None)
        )
        assert await until(lambda: workflows_named(focus, "enrich_task", f"enrich:{day.b}:"))
        assert await until(
            lambda: task_row(focus, day.b)["enrichment_status"] in {"done", "failed"}
        )

    prepared = task_row(focus, day.b)
    assert prepared["enrichment_status"] == "done"
    assert prepared["first_action"] == result["first_action"]
    assert prepared["first_action_source"] == "agent"
    assert workflows_named(focus, "enrich_task", f"enrich:{day.c}:") == []
    assert workflows_named(focus, "enrich_task", f"enrich:{day.a}:") == []
