"""projects pure rules: no I/O, `now` and `tz` passed in (interface stub, P0-17)."""

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


@dataclass(frozen=True, slots=True)
class TaskFacts:
    status: str
    due_on: date | None
    deleted: bool = False


@dataclass(frozen=True, slots=True)
class HealthFacts:
    waiting_on_human: int
    overdue: int


class CodeLocationError(ValueError):
    code = "invalid_code_location"


class CodeLocationConflict(CodeLocationError):  # noqa: N818  # the plan's name
    code = "code_location_conflict"


class InvalidCodeLocation(CodeLocationError):  # noqa: N818  # pairs with the plan's name
    code = "invalid_code_location"


GATED_DEFAULT: Final[tuple[str, ...]] = ()
ALLOWED_DEFAULT: Final[tuple[str, ...]] = ()


def local_today(now: datetime, tz: ZoneInfo) -> date:
    raise NotImplementedError


def is_overdue(status: str, due_on: date | None, today: date) -> bool:
    raise NotImplementedError


def summarize(tasks: Iterable[TaskFacts], today: date) -> HealthFacts:
    raise NotImplementedError


def project_health(f: HealthFacts) -> Health:
    raise NotImplementedError


def next_milestone(deadline: date | None, next_open_due: date | None, today: date) -> date | None:
    raise NotImplementedError


def validate_code_location(code_path: str | None, repo_url: str | None) -> None:
    raise NotImplementedError
