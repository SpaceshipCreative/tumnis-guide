"""Imported by the kill test's worker subprocesses (`run_worker --import`): every pipeline
step that starts appends its name to the file named by `KNOWLEDGE_STEP_LOG`, one line per
step, so the test sees across both processes which steps ran (T-P1-16-07)."""

import os
from collections.abc import Awaitable, Callable
from typing import Any

from tumnis.modules.knowledge import pipeline


def _logged(name: str, step: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    async def run(*args: Any, **kwargs: Any) -> Any:
        with open(os.environ["KNOWLEDGE_STEP_LOG"], "a") as log:  # noqa: ASYNC230
            log.write(f"{name}\n")
        return await step(*args, **kwargs)

    return run


for _name in pipeline.STEPS:
    setattr(pipeline, _name, _logged(_name, getattr(pipeline, _name)))
