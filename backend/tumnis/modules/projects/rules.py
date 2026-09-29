"""projects pure rules: no I/O, `now` and `tz` passed in (P0-17).

Health (FR-1.1): a project is blocked while an open task waits on the human (status
`waiting_on_human` only: `in_review` is work the agent finished, and counting it would block
every project with a pending result), at risk while an open task is overdue, else on track.
Overdue means open and due before the workspace's local today; due today is not overdue.
The facts come from the tasks module through the stats source (projects cannot import
tasks), aggregated to `HealthFacts`.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo


class Health(StrEnum):
    BLOCKED = "blocked"
    AT_RISK = "at_risk"
    ON_TRACK = "on_track"


OPEN_STATUSES: Final = frozenset(
    {"backlog", "today", "in_progress", "waiting_on_human", "in_review"}
)
WAITING_ON_HUMAN: Final = "waiting_on_human"


@dataclass(frozen=True, slots=True)
class TaskFacts:
    """What health needs from one task; the tasks module builds these."""

    status: str
    due_on: date | None
    deleted: bool = False


@dataclass(frozen=True, slots=True)
class HealthFacts:
    """The aggregate; the tasks module's SQL returns exactly this."""

    waiting_on_human: int
    overdue: int


def local_today(now: datetime, tz: ZoneInfo) -> date:
    """The workspace's calendar day at `now`, which must be timezone-aware."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    return now.astimezone(tz).date()


def is_overdue(status: str, due_on: date | None, today: date) -> bool:
    return status in OPEN_STATUSES and due_on is not None and due_on < today


def summarize(tasks: Iterable[TaskFacts], today: date) -> HealthFacts:
    """Counts over live tasks; done and trashed tasks never count."""
    live = [t for t in tasks if not t.deleted]
    return HealthFacts(
        waiting_on_human=sum(1 for t in live if t.status == WAITING_ON_HUMAN),
        overdue=sum(1 for t in live if is_overdue(t.status, t.due_on, today)),
    )


def project_health(f: HealthFacts) -> Health:
    """Blocked > at_risk > on_track: the first rule in the precedence tuple that holds."""
    precedence = ((Health.BLOCKED, f.waiting_on_human), (Health.AT_RISK, f.overdue))
    return next((health for health, count in precedence if count > 0), Health.ON_TRACK)


def next_milestone(deadline: date | None, next_open_due: date | None, today: date) -> date | None:
    """The earlier of the project deadline and the next open task due on or after today
    (plan default: the PRD has no milestone entity). A past deadline still shows."""
    upcoming = next_open_due if next_open_due is not None and next_open_due >= today else None
    candidates = [d for d in (deadline, upcoming) if d is not None]
    return min(candidates) if candidates else None


# --- Code location (FR-2.1): a path on the agent server or a repo URL, never both ---------


class CodeLocationError(ValueError):
    """Answered 422 with `code`."""

    code = "invalid_code_location"


class CodeLocationConflict(CodeLocationError):  # noqa: N818  # the plan's name
    code = "code_location_conflict"


class InvalidCodeLocation(CodeLocationError):  # noqa: N818  # pairs with the plan's name
    code = "invalid_code_location"


_HTTPS_URL: Final = re.compile(r"^https://(?P<authority>[^/?#]+)/[^\s]+$")
_SSH_URL: Final = re.compile(r"^ssh://(?:[A-Za-z0-9._-]+@)?[A-Za-z0-9.-]+(?::\d+)?/[^\s]+$")
_SCP_URL: Final = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[^\s/][^\s]*$")


def _check_path(code_path: str) -> None:
    if not code_path.startswith("/"):
        raise InvalidCodeLocation("code_path must be an absolute POSIX path")
    if any(part == ".." for part in code_path.split("/")):
        raise InvalidCodeLocation("code_path must not contain '..' segments")


def _check_url(repo_url: str) -> None:
    https = _HTTPS_URL.match(repo_url)
    if https is not None:
        if "@" in https.group("authority"):
            raise InvalidCodeLocation("repo_url must not carry credentials")
        return
    if _SSH_URL.match(repo_url) or _SCP_URL.match(repo_url):
        return
    raise InvalidCodeLocation("repo_url must be an https:// or ssh git URL")


def validate_code_location(code_path: str | None, repo_url: str | None) -> None:
    """Raises CodeLocationConflict for both; InvalidCodeLocation for a relative path, a
    `..` segment, or a URL that is not https/ssh or carries credentials."""
    if code_path is not None and repo_url is not None:
        raise CodeLocationConflict("set code_path or repo_url, not both")
    if code_path is not None:
        _check_path(code_path)
    if repo_url is not None:
        _check_url(repo_url)


# --- Default approval policy (FR-5.6) and runaway limits (SAF-5) --------------------------

GATED_DEFAULT: Final = (
    "send_email",
    "push_main",
    "merge_main",
    "force_push",
    "deploy_production",
    "proxmox_delete_guest",
    "proxmox_rollback_snapshot",
    "proxmox_storage_change",
    "proxmox_network_change",
    "spend_money",
    "delete_files",
)
ALLOWED_DEFAULT: Final = (
    "push_feature_branch",
    "open_pull_request",
    "trigger_preview_deploy",
    "proxmox_create_guest",
    "proxmox_start_guest",
    "create_draft",
    "read",
)
MAX_CONCURRENT_RUNS_DEFAULT: Final = 2
MAX_RUN_MINUTES_DEFAULT: Final = 60
MAX_TASKS_PER_RUN_DEFAULT: Final = 20
SUBTASK_THRESHOLD_DEFAULT: Final = 30  # minutes, when neither project nor workspace sets one
