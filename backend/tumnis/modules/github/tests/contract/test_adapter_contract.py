"""The GitHub status port's contract (P2-13, FR-12.1): the fake and the real client over
the recorded answers behave the same (AGENTS.md: fakes obey the real contract), including
conditional requests (an ETag the answer carried comes back 304 Not Modified)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.github.tests.replay import (
    Replay,
    load,
    recorded_api,
    recording_names,
)

if TYPE_CHECKING:
    from tumnis.modules.github.adapters.port import GitHubStatus

COVE_5 = ("cove-example", "web", 5)  # the not_modified recording: its ETags answer 304


def _all() -> Replay:
    return Replay(*(load(name) for name in recording_names()))


async def _read_all(subject: GitHubStatus, owner: str, repo: str, number: int) -> dict[str, Any]:
    pull = await subject.get_pull(owner, repo, number, None)
    assert pull.value is not None
    sha = pull.value.head.sha
    statuses = await subject.get_combined_status(owner, repo, sha, None)
    runs = await subject.list_check_runs(owner, repo, sha, None)
    reviews = await subject.list_reviews(owner, repo, number, None)
    return {
        "pull": pull.value,
        "statuses": statuses.value,
        "check_runs": runs.value,
        "reviews": reviews.value,
    }


@pytest.mark.contract
@pytest.mark.req("FR-12.1")
@pytest.mark.wp("P2-13")
@pytest.mark.xfail(strict=True, reason="spec:P2-13")
async def test_fake_and_recordings_agree() -> None:
    """T-P2-13-09
    The shared contract suite runs for the fake and for the real client over the
    recordings (the classes above); and for every recorded pull request both answer the
    same pull, commit statuses, check runs and reviews, so tests written against the fake
    hold for GitHub.
    """
    from tumnis.core.adapters.registry import contract_impls  # noqa: PLC0415
    from tumnis.modules.github.adapters.fake import FakeGitHubStatus  # noqa: PLC0415

    assert {"fake", "recorded"} <= contract_impls()["github.status"]
    fake, real = FakeGitHubStatus(), recorded_api(_all())
    for name in recording_names():
        pull = load(name)["pull"]
        assert await _read_all(fake, **pull) == await _read_all(real, **pull), name
