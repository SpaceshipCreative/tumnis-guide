"""The fake runner's stored scripts (P2-04, R-37, `POST /v1/test/fakes/runner/script`): the
phase 1 shape (a recorded answer for a profile and skill) and the phase 2 shape (the steps
of each run of one task, by its title), and what the route refuses."""

from __future__ import annotations

from typing import Any

import pytest

PHASE_1 = {
    "profile": "tumnis-master",
    "skill": "plan",
    "result": "plan__monday_four_picks.result.json",
    "delay_ms": 0,
}
RESULT = {
    "outcome": "done",
    "summary": "Fixed the footer link target",
    "files_touched": [{"path": "src/footer.tsx", "change": "modified"}],
    "links": [],
}
TASK_RUNS = {
    "task_title": "Fix footer link",
    "runs": [
        [
            {"stream": {"kind": "log", "text": "Reading the footer component"}},
            {"stream": {"kind": "tool_call", "text": "git.checkout"}},
            {"stream": {"kind": "file_touched", "text": "src/footer.tsx"}},
            {
                "upload_artifact": {
                    "name": "notes.md",
                    "media_type": "text/markdown",
                    "content": "# Footer notes\n",
                }
            },
            {"result": RESULT},
        ],
        [{"result": {**RESULT, "summary": "The footer link now opens in a new tab"}}],
    ],
}


def _parse(body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    from tumnis.modules.agents.adapters.fake import parse_runner_script  # noqa: PLC0415

    return parse_runner_script(body)


@pytest.mark.req("REL-7")
@pytest.mark.wp("P2-04")
def test_phase_one_script_is_keyed_by_profile_and_skill() -> None:
    """The phase 1 body (`runnerScript(profile, skill, result)`) is stored under its
    profile and skill."""
    key, stored = _parse(PHASE_1)
    assert key == "tumnis-master/plan"
    assert stored["result"] == "plan__monday_four_picks.result.json"
    assert stored["delay_ms"] == 0


@pytest.mark.req("FR-5.5")
@pytest.mark.wp("P2-04")
def test_task_script_is_keyed_by_task_title_and_keeps_every_run() -> None:
    """The phase 2 body (`script(taskTitle, runs)`) is stored under the task's title with
    its runs in order, none played yet."""
    key, stored = _parse(TASK_RUNS)
    assert key == "task:Fix footer link"
    assert len(stored["runs"]) == 2
    first_step = {"stream": {"kind": "log", "text": "Reading the footer component"}}
    assert stored["runs"][0][0] == first_step
    assert stored["runs"][1][0]["result"]["summary"] == "The footer link now opens in a new tab"
    assert stored["played"] == 0


@pytest.mark.req("FR-5.5")
@pytest.mark.wp("P2-04")
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"task_title": "", "runs": []},
        {"task_title": "Fix footer link", "runs": [[{"stream": {"kind": "log"}}]]},
        {"task_title": "Fix footer link", "runs": [[{"stream": {"kind": "shout", "text": "x"}}]]},
        {
            "task_title": "Fix footer link",
            "runs": [[{"stream": {"kind": "log", "text": "x"}, "result": RESULT}]],
        },
        {"task_title": "Fix footer link", "runs": [[{"result": {"outcome": "done"}}]]},
        {**PHASE_1, "delay_ms": -1},
        {**PHASE_1, "unexpected": True},
    ],
)
def test_runner_script_refuses_what_the_fake_cannot_play(body: dict[str, Any]) -> None:
    """No body, an empty title, a step the fake cannot play (a question belongs to P2-05),
    a step naming two actions, a result without a summary, a negative delay or an unknown
    key: the route answers 422."""
    with pytest.raises(ValueError):  # noqa: PT011  # pydantic's ValidationError included
        _parse(body)
