"""A task token's comment follows its run's taint (P2-08, SAF-1; P2-02 left it here)."""

from __future__ import annotations

import pytest

TOKEN = "task_token:01950000-0000-7000-8000-000000000002"
KEY = "api_key:01950000-0000-7000-8000-000000000003"
PERSON = "user:01950000-0000-7000-8000-000000000001"


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-08")
@pytest.mark.parametrize(
    ("author", "run_tainted", "tainted"),
    [
        (TOKEN, True, True),
        (TOKEN, False, False),
        (KEY, False, True),
        (PERSON, False, False),
        (PERSON, True, False),
    ],
)
def test_a_task_token_comment_is_tainted_when_its_run_is(
    author: str, run_tainted: bool, tainted: bool
) -> None:
    """A comment written with a task token is tainted exactly when that token's run is; a
    key with no run always taints (R-31); a person's comment never does."""
    from tumnis.modules.agents.rules import comment_tainted  # noqa: PLC0415

    assert comment_tainted(author, run_tainted=run_tainted) is tainted
