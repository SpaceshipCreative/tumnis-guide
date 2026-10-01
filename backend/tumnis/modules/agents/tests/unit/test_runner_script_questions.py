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


@pytest.mark.req("FR-5.5")
@pytest.mark.wp("P2-04")
async def test_answer_that_overflows_the_summary_is_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A summary at its limit grows past it once `{answer}` is replaced: the playback logs
    the invalid result and posts nothing, instead of failing the background task."""
    from uuid import UUID  # noqa: PLC0415

    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.modules.agents import api, fake_play  # noqa: PLC0415

    posted: list[Any] = []

    async def accept_result(*args: Any, **kwargs: Any) -> None:
        posted.append(args)

    monkeypatch.setattr(api, "accept_result", accept_result)
    summary = "x" * (20_000 - len("{answer}")) + "{answer}"
    result = fake_play._with_answer({"outcome": "done", "summary": summary}, "Turquoise")
    ctx = WorkspaceContext(UUID(int=1), fake_play.FAKE_RUNNER_ACTOR)
    await fake_play._result(ctx, UUID(int=2), result)
    assert posted == []
