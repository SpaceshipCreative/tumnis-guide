"""github pure rules (P2-13, FR-12.1): pull request URLs, the repository allow-list, the
combined check and review states a task card shows, and the views the read-only client
returns. No I/O; the webhook signature check lives in `signature.py` (it needs `hmac`,
which the rules allow-list does not have).

The views hold only what the card needs and ignore every other field GitHub sends, so a
new field in the API changes nothing here.
"""

import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any, Final, Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator

CheckState = Literal["pending", "green", "red", "none"]
ReviewSummary = Literal["approved", "changes_requested", "review_required", "none"]
PrState = Literal["open", "merged", "closed"]

# Commit statuses (`state`) and check run conclusions that make a pull request red or green;
# anything else, or a check run not completed, is still pending.
RED_STATUSES: Final = frozenset({"failure", "error"})
RED_CONCLUSIONS: Final = frozenset(
    {"failure", "error", "cancelled", "timed_out", "action_required", "startup_failure"}
)
GREEN_STATUSES: Final = frozenset({"success"})
GREEN_CONCLUSIONS: Final = frozenset({"success", "neutral", "skipped"})

_PR_URL: Final = re.compile(
    r"(?i:https://(?:www\.)?github\.com)/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100})"
    r"/pull/([0-9]{1,9})(?:[/?#].*)?"
)
_NOT_REPO_NAMES: Final = frozenset({".", ".."})


# --- Views (the parts of GitHub's answers a card uses) ------------------------------------


class Login(BaseModel):
    login: str


class Ref(BaseModel):
    sha: str


class PullView(BaseModel):
    number: int
    state: Literal["open", "closed"]
    merged: bool = False
    merged_at: str | None = None
    draft: bool = False
    title: str
    html_url: str
    head: Ref
    requested_reviewers: list[Login] = Field(default_factory=list)


class StatusView(BaseModel):
    context: str
    state: str


class CheckRunView(BaseModel):
    name: str
    status: str
    conclusion: str | None = None


class ReviewView(BaseModel):
    user: Login | None = None  # GitHub sends null for a deleted account
    state: str
    submitted_at: AwareDatetime | None = None


class PrRef(BaseModel, frozen=True):
    """A pull request by repository and number; owner and repository are lower-cased, as
    GitHub matches them without regard to case."""

    owner: str
    repo: str
    number: int = Field(ge=1)

    @model_validator(mode="before")
    @classmethod
    def _lower(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = {
                **data,
                **{k: str(data[k]).lower() for k in ("owner", "repo") if k in data},
            }
        return data

    @property
    def external_id(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"


class Snapshot(BaseModel):
    """Everything read about one pull request, with the ETag of each read (`pull`,
    `status`, `check_runs`, `reviews`) for the next conditional request."""

    pull: PullView
    statuses: list[StatusView]
    check_runs: list[CheckRunView]
    reviews: list[ReviewView]
    etags: dict[str, str] = Field(default_factory=dict)


# --- Rules -------------------------------------------------------------------------------


def parse_pr_url(url: str) -> tuple[str, str, int] | None:
    """(owner, repo, number) of a github.com pull request URL, whatever follows the number
    (a trailing slash, `/files`, a query, a fragment); None for anything else: another host
    (GitHub Enterprise included), another scheme, userinfo or a port, an issue URL, a
    number that is not a positive integer."""
    found = _PR_URL.fullmatch(url.strip())
    if found is None:
        return None
    owner, repo, number = found.groups()
    if repo in _NOT_REPO_NAMES or int(number) < 1:
        return None
    return owner, repo, int(number)


def allowed_repo(owner: str, repo: str, allow_list: Iterable[str]) -> bool:
    """Whether `owner/repo` is on the allow-list: exact names or `owner/*`, compared without
    regard to case. An empty list allows nothing."""
    wanted = {f"{owner}/{repo}".lower(), f"{owner}/*".lower()}
    return any(entry.strip().lower() in wanted for entry in allow_list)


def combine_checks(
    statuses: Sequence[StatusView], check_runs: Sequence[CheckRunView]
) -> CheckState:
    """Red if any status failed or errored or any check run ended in failure, error,
    cancelled, timed_out, action_required or startup_failure; else pending if anything is
    still queued, running or unknown; else green (at least one, all success, neutral or
    skipped); none when there are no checks at all."""
    marks: list[CheckState] = [
        "red" if s.state in RED_STATUSES else "green" if s.state in GREEN_STATUSES else "pending"
        for s in statuses
    ]
    for run in check_runs:
        if run.status != "completed" or run.conclusion is None:
            marks.append("pending")
        elif run.conclusion in RED_CONCLUSIONS:
            marks.append("red")
        elif run.conclusion in GREEN_CONCLUSIONS:
            marks.append("green")
        else:
            marks.append("pending")
    if not marks:
        return "none"
    if "red" in marks:
        return "red"
    return "pending" if "pending" in marks else "green"


_EARLIEST: Final = datetime.min.replace(tzinfo=UTC)


def review_state(reviews: Sequence[ReviewView]) -> ReviewSummary:
    """Each reviewer's standing decision is their latest approval or change request (a
    comment never overrides one; a dismissal clears it). Changes requested wins over
    approved; reviews without a standing decision leave the pull request needing review;
    no reviews at all is `none`."""
    seen = [r for r in reviews if r.state != "PENDING" and r.user is not None]
    if not seen:
        return "none"
    standing: dict[str, str] = {}
    ordered = sorted(enumerate(seen), key=lambda p: (p[1].submitted_at or _EARLIEST, p[0]))
    for _, review in ordered:
        assert review.user is not None  # noqa: S101  # filtered above
        if review.state in {"APPROVED", "CHANGES_REQUESTED"}:
            standing[review.user.login] = review.state
        elif review.state == "DISMISSED":
            standing.pop(review.user.login, None)
    decisions = set(standing.values())
    if "CHANGES_REQUESTED" in decisions:
        return "changes_requested"
    return "approved" if "APPROVED" in decisions else "review_required"


def pr_state(pull: PullView) -> PrState:
    if pull.merged or pull.merged_at is not None:
        return "merged"
    return "open" if pull.state == "open" else "closed"


def pr_status(ref: PrRef, snapshot: Snapshot) -> dict[str, Any]:
    """`checks.pr_status` of the pull request's artifact: what its card shows. An open pull
    request nobody reviewed but with reviewers requested reads `review_required`."""
    pull = snapshot.pull
    review = review_state(snapshot.reviews)
    if review == "none" and pull.requested_reviewers and pr_state(pull) == "open":
        review = "review_required"
    return {
        "repo": ref.full_name,
        "number": ref.number,
        "title": pull.title,
        "draft": pull.draft,
        "head_sha": pull.head.sha,
        "checks": combine_checks(snapshot.statuses, snapshot.check_runs),
        "review": review,
    }
