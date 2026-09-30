"""github event payload models (P2-13); `events.py` re-exports them. They live apart so
`api.py` can emit them without importing `events.py`.

- `github.fetched`: one refresh of a pull request read GitHub: `requests` made, of which
  `not_modified` were answered 304 (those do not count against GitHub's rate limit). The
  usage counters `github_requests` and `github_not_modified` follow it.
"""

from typing import ClassVar, Literal

from pydantic import Field

from tumnis.core.events import EventPayload, event_type


@event_type("github.fetched", 1)
class GitHubFetchedV1(EventPayload):
    event_name: ClassVar[str] = "github.fetched"
    schema_version: Literal[1] = 1
    requests: int = Field(ge=0)
    not_modified: int = Field(ge=0)
