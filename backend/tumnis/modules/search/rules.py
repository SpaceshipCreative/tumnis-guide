"""search pure rules: no I/O, `now` and `tz` passed in (P0-20).

Interfaces only until the P0-20 spec tests turn green.
"""

from dataclasses import dataclass
from typing import Final

MAX_TERMS: Final = 8  # plan default
PROJECT_BOOST: Final = 2.0  # plan default
RECENCY_DAYS: Final = 14.0  # plan default: score / (1 + age_days / 14)


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    phrases: tuple[str, ...]
    terms: tuple[str, ...]
    prefix: str | None


def parse_query(q: str) -> ParsedQuery:
    raise NotImplementedError


def tsquery_parts(p: ParsedQuery) -> list[tuple[str, str]]:
    raise NotImplementedError
