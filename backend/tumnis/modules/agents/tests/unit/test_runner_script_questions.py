"""The fake runner's question step (Scott decision 55, R-37): a task script may ask the
human (`{"ask_human": {"prompt", "choices"?}}`) mid-run, as A2.2's UI journey scripts it
(frontend/e2e/phase2.ts `QUESTION_RUNS`)."""

from __future__ import annotations

from typing import Any

import pytest

QUESTION_RUNS = {
    "task_title": "Fix footer link",
    "runs": [
        [
            {"stream": {"kind": "log", "text": "Reading the footer component"}},
            {"ask_human": {"prompt": "Which footer color?"}},
            {"result": {"outcome": "done", "summary": "Footer colour set to {answer}"}},
        ],
        [{"ask_human": {"prompt": "Which shade?", "choices": ["Navy", "Teal"]}}],
    ],
}


def _parse(body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    from tumnis.modules.agents.adapters.fake import parse_runner_script  # noqa: PLC0415

    return parse_runner_script(body)


@pytest.mark.req("FR-5.7")
@pytest.mark.wp("P2-04")
@pytest.mark.xfail(strict=True, reason="spec:SPEC-54-55")
def test_task_script_keeps_a_question_step() -> None:
    """A question step is stored as given, with its choices when it has some."""
    key, stored = _parse(QUESTION_RUNS)
    assert key == "task:Fix footer link"
    assert stored["runs"][0][1] == {"ask_human": {"prompt": "Which footer color?"}}
    assert stored["runs"][1][0] == {
        "ask_human": {"prompt": "Which shade?", "choices": ["Navy", "Teal"]}
    }


@pytest.mark.req("FR-5.7")
@pytest.mark.wp("P2-04")
@pytest.mark.parametrize(
    "step",
    [
        {"ask_human": {}},
        {"ask_human": {"prompt": ""}},
        {"ask_human": {"prompt": "Which?", "choices": [""]}},
        {"ask_human": {"prompt": "Which?", "deadline": "never"}},
        {"ask_human": {"prompt": "Which?"}, "stream": {"kind": "log", "text": "x"}},
    ],
)
def test_question_step_refuses_what_the_fake_cannot_ask(step: dict[str, Any]) -> None:
    """A question with no prompt, an empty prompt or choice, an unknown key, or a step
    naming a question and another action: refused."""
    with pytest.raises(ValueError):  # noqa: PT011  # pydantic's ValidationError included
        _parse({"task_title": "Fix footer link", "runs": [[step]]})
