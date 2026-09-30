"""The GitHub status port's contract (P2-13, FR-12.1): the fake and the real client over
the recorded answers behave the same (AGENTS.md: fakes obey the real contract), including
conditional requests (an ETag the answer carried comes back 304 Not Modified)."""

from __future__ import annotations

from typing import Any

import pytest

from tumnis.core.adapters.contract import AdapterContract
from tumnis.core.adapters.errors import AdapterRejected
from tumnis.modules.github.adapters.fake import FakeGitHubStatus
from tumnis.modules.github.adapters.port import GitHubStatus
from tumnis.modules.github.tests.replay import (
    Replay,
    first_round,
    load,
    recorded_api,
    recording_names,
)

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


class GitHubStatusContract(AdapterContract[GitHubStatus]):
    port, adapter_name = GitHubStatus, "github.status"

    async def test_every_recorded_pull_reads_back(self, subject: GitHubStatus) -> None:
        for name in recording_names():
            rec = load(name)
            read = await _read_all(subject, **rec["pull"])
            pull, statuses, runs, reviews = (ex["body"] for ex in first_round(rec))
            assert (read["pull"].number, read["pull"].head.sha, read["pull"].html_url) == (
                pull["number"],
                pull["head"]["sha"],
                pull["html_url"],
            ), name
            assert [(s.context, s.state) for s in read["statuses"]] == [
                (s["context"], s["state"]) for s in statuses["statuses"]
            ], name
            assert [(r.name, r.status, r.conclusion) for r in read["check_runs"]] == [
                (r["name"], r["status"], r["conclusion"]) for r in runs["check_runs"]
            ], name
            assert [(r.user.login if r.user else None, r.state) for r in read["reviews"]] == [
                (r["user"]["login"] if r["user"] else None, r["state"]) for r in reviews
            ], name

    async def test_an_etag_answers_not_modified(self, subject: GitHubStatus) -> None:
        owner, repo, number = COVE_5
        first = await subject.get_pull(owner, repo, number, None)
        assert first.value is not None
        assert first.etag
        assert not first.not_modified
        again = await subject.get_pull(owner, repo, number, first.etag)
        assert again.value is None
        assert again.not_modified
        sha = first.value.head.sha
        runs = await subject.list_check_runs(owner, repo, sha, None)
        assert runs.etag
        held = await subject.list_check_runs(owner, repo, sha, runs.etag)
        assert held.value is None
        assert held.not_modified

    async def test_unknown_pull_is_rejected(self, subject: GitHubStatus) -> None:
        with pytest.raises(AdapterRejected) as caught:
            await subject.get_pull("acme-example", "site", 999, None)
        assert not caught.value.retryable
        with pytest.raises(AdapterRejected):
            await subject.list_reviews("nobody-example", "none", 1, None)

    async def test_health_is_ok(self, subject: GitHubStatus) -> None:
        assert await subject.health() == "ok"


@pytest.mark.contract
@pytest.mark.req("FR-12.1")
@pytest.mark.wp("P2-13")
class TestGitHubStatusFake(GitHubStatusContract):
    impl = "fake"

    @pytest.fixture
    def subject(self) -> GitHubStatus:
        return FakeGitHubStatus()


@pytest.mark.contract
@pytest.mark.req("FR-12.1")
@pytest.mark.wp("P2-13")
class TestGitHubStatusRecorded(GitHubStatusContract):
    impl = "recorded"

    @pytest.fixture
    def subject(self) -> GitHubStatus:
        return recorded_api(_all())


@pytest.mark.contract
@pytest.mark.req("FR-12.1")
@pytest.mark.wp("P2-13")
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
