"""Shared pure types. rules.py may import this module, so it does no I/O and imports
only what the rules allow-list permits (T-P0-01-09)."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal, NewType
from uuid import UUID

WorkspaceId = NewType("WorkspaceId", UUID)

# "system" or "<kind>:<uuid>"; the same shape P0-06's ACTOR_CHECK enforces on created_by.
ActorRef = NewType("ActorRef", str)

SYSTEM_ACTOR: Final = ActorRef("system")
ACTOR_REF_PATTERN: Final = re.compile(r"^(system|(user|api_key|task_token|device):[0-9a-f-]{36})$")

# Where this deployment runs (A11 DEPLOYMENT_MODE): the SSRF guard allows the private LAN
# only when self-hosted (P0-16).
DeploymentMode = Literal["self-hosted", "hosted"]


@dataclass(frozen=True, order=True)
class Interval:
    """A span of time [start, end): aware instants, stored in UTC, the end after the start
    (P1-10). The calendar's free blocks and the planner's working window are Intervals; it
    lives here so every module's rules can use it."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.utcoffset() is None or self.end.utcoffset() is None:
            raise ValueError("an Interval needs aware datetimes")
        if self.end <= self.start:
            raise ValueError(f"an Interval ends after it starts: {self.start} to {self.end}")
        object.__setattr__(self, "start", self.start.astimezone(UTC))
        object.__setattr__(self, "end", self.end.astimezone(UTC))

    @property
    def minutes(self) -> int:
        """Whole minutes, rounded down."""
        return int((self.end - self.start).total_seconds() // 60)
