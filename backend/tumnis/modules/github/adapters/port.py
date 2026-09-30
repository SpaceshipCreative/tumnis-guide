"""The GitHub status port (P2-13, FR-12.1): the four reads the github module makes, as typed
views. Read-only by construction: there is no method that writes to GitHub (agents do that
through their own MCP server), and `github_status.py` sends nothing but GET.

Every read takes the ETag the last answer carried. GitHub answers 304 Not Modified when
nothing changed, which does not count against the rate limit
([best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)):
the answer is then `Fetched(None, etag)` and `not_modified` is true.
"""

from dataclasses import dataclass
from typing import Protocol

from tumnis.core.adapters.registry import Health
from tumnis.modules.github.rules import CheckRunView, PullView, ReviewView, StatusView


@dataclass(frozen=True)
class Fetched[T]:
    """One read: `value` is None exactly when GitHub answered 304 (the caller's copy is
    current); `etag` is the answer's ETag, to send with the next read."""

    value: T | None
    etag: str | None

    @property
    def not_modified(self) -> bool:
        return self.value is None


class GitHubStatus(Protocol):
    async def get_pull(
        self, owner: str, repo: str, number: int, etag: str | None
    ) -> Fetched[PullView]:
        """GET /repos/{owner}/{repo}/pulls/{number}."""
        ...

    async def get_combined_status(
        self, owner: str, repo: str, ref: str, etag: str | None
    ) -> Fetched[list[StatusView]]:
        """The commit statuses of `ref` (GET /repos/{owner}/{repo}/commits/{ref}/status)."""
        ...

    async def list_check_runs(
        self, owner: str, repo: str, ref: str, etag: str | None
    ) -> Fetched[list[CheckRunView]]:
        """GET /repos/{owner}/{repo}/commits/{ref}/check-runs."""
        ...

    async def list_reviews(
        self, owner: str, repo: str, number: int, etag: str | None
    ) -> Fetched[list[ReviewView]]:
        """GET /repos/{owner}/{repo}/pulls/{number}/reviews."""
        ...

    async def health(self) -> Health: ...
