"""What the phase 1 skills read and reply (P1-05, R-02, R-24): the bodies of the one
`TaskPacket` for `enrich` (project template) and `plan` (master), and the JSON each skill
replies with.

Every model is a `@versioned` payload with an integer `schema_version`; `make gen` writes
`schemas/enrichment/v1/{request,result}.json` and `schemas/planning/v1/{request,result}.json`,
which the skill harness validates replies against. A reply carries `"schema_version": 1`.

The limits are plan defaults. The cross-field rules a schema cannot say (an estimate only
for human and hybrid tasks, a split only for hybrid ones, picks only from the candidates)
are `rules.enrichment_errors` and `rules.planning_errors`. P1-08 and P1-11 build the
requests; P1-17 fills `passages`.
"""

from datetime import date
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from tumnis.core.limits import MAX_ESTIMATE_MINUTES
from tumnis.core.schemas import VersionedPayload, versioned
from tumnis.modules.agents.rules import Label

__all__ = [
    "ESTIMATE_RANGE",
    "EnrichProject",
    "EnrichTask",
    "EnrichmentRequest",
    "EnrichmentResult",
    "EstimateHistoryItem",
    "EventSummary",
    "HybridSplit",
    "Interval",
    "Label",
    "LabelRevision",
    "Passage",
    "PlanCandidate",
    "PlanPick",
    "PlanProject",
    "PlanningRequest",
    "PlanningResult",
    "ProjectAgentEntry",
]

# Minutes of human time; the lower bound 5 is a plan default, the upper one R-11's cap.
ESTIMATE_RANGE: Final = (5, MAX_ESTIMATE_MINUTES)
BRIEF_MAX: Final = 8_000
BRIEF_EXCERPT_MAX: Final = 600
MAX_PASSAGES: Final = 8
MAX_HISTORY: Final = 10
MAX_CANDIDATES: Final = 60
MAX_PICKS: Final = 5

Priority = Literal["low", "normal", "high", "urgent"]  # as tasks.api.Priority
Health = Literal["on_track", "at_risk", "blocked"]  # as projects' Health
MissingField = Literal["first_action", "acceptance_criteria", "estimate_minutes"]
Title = Annotated[str, StringConstraints(min_length=1, max_length=500)]
LongText = Annotated[str, StringConstraints(max_length=8_000)]
Line = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class _Part(BaseModel):
    """A part of a skill body: frozen, no unknown fields, no version of its own."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# --- enrich (project template) ----------------------------------------------------------


class EnrichTask(_Part):
    """The task as it is now; the fields named in `missing` are the ones to fill."""

    id: UUID
    title: Title
    label: Label | None  # None: still pending (R-08); no estimate is asked for then
    label_reason: str | None = Field(default=None, max_length=200)
    parent_title: Title | None = None
    due_on: date | None = None
    priority: Priority = "normal"
    first_action: LongText | None = None
    acceptance_criteria: LongText | None = None
    estimate_minutes: int | None = Field(default=None, ge=1, le=MAX_ESTIMATE_MINUTES)


class EnrichProject(_Part):
    name: str = Field(min_length=1, max_length=120)
    client: str | None = Field(default=None, max_length=120)
    goal: str | None = Field(default=None, max_length=280)


class Passage(_Part):
    """A knowledge passage (P1-17 fills these from `knowledge.api.passages_for`): the
    chunk it was cut from (optional, so a request written by hand without one still reads),
    and its document, title, heading path and page."""

    chunk_id: UUID | None = None
    document_id: UUID
    title: Title
    heading_path: list[str] = Field(max_length=12)
    page: int | None = Field(ge=1)
    text: LongText


class EstimateHistoryItem(_Part):
    """A finished Human or Hybrid task of the project: what was estimated, what it took."""

    title: str = Field(min_length=1, max_length=120)
    label: Label
    estimate_minutes: int = Field(ge=1, le=MAX_ESTIMATE_MINUTES)
    actual_minutes: int = Field(ge=0)


@versioned("enrichment", "request", 1)
class EnrichmentRequest(VersionedPayload):
    schema_version: Literal[1] = 1
    task: EnrichTask
    missing: list[MissingField] = Field(min_length=1, max_length=3)
    project: EnrichProject
    brief: str = Field(max_length=BRIEF_MAX)
    passages: list[Passage] = Field(max_length=MAX_PASSAGES)
    estimate_history: list[EstimateHistoryItem] = Field(max_length=MAX_HISTORY)


class LabelRevision(_Part):
    label: Label
    reason: str = Field(min_length=1, max_length=80)


class HybridSplit(_Part):
    ai_portion: str = Field(min_length=1, max_length=200)
    human_portion: str = Field(min_length=1, max_length=200)


@versioned("enrichment", "result", 1)
class EnrichmentResult(VersionedPayload):
    """The `enrich` skill's reply. `estimate_minutes` is minutes of human time: for a
    hybrid task the human portion only, and never for an ai task."""

    schema_version: Literal[1] = 1
    task_id: UUID
    first_action: str = Field(min_length=8, max_length=200)
    acceptance_criteria: list[Line] = Field(min_length=1, max_length=6)
    estimate_minutes: int | None = Field(default=None, ge=ESTIMATE_RANGE[0], le=ESTIMATE_RANGE[1])
    label_revision: LabelRevision | None = None
    hybrid_split: HybridSplit | None = None


# --- plan (master) ----------------------------------------------------------------------


class Interval(_Part):
    """A span of time, UTC; the planner's `calendar.rules.Interval` on the wire (P1-09)."""

    start: AwareDatetime
    end: AwareDatetime


class PlanCandidate(_Part):
    task_id: UUID
    project_id: UUID
    project_name: str = Field(min_length=1, max_length=120)
    title: Title
    label: Label | None  # None: the label is still pending (R-08)
    estimate_minutes: int | None = Field(ge=1, le=MAX_ESTIMATE_MINUTES)
    due_on: date | None
    priority: Priority
    rollover_count: int = Field(ge=0)
    first_action: LongText | None
    age_days: int = Field(ge=0)


class PlanProject(_Part):
    id: UUID
    name: str = Field(min_length=1, max_length=120)
    health: Health
    next_milestone: date | None
    brief_excerpt: str = Field(max_length=BRIEF_EXCERPT_MAX)


class ProjectAgentEntry(_Part):
    """One row of the master's registry of project agents (filled by P1-06)."""

    project_id: UUID
    project_name: str = Field(min_length=1, max_length=120)
    profile: str = Field(max_length=63)
    status: str = Field(max_length=32)
    runner: str | None = Field(max_length=63)


class EventSummary(_Part):
    title: str = Field(max_length=120)
    start: AwareDatetime
    end: AwareDatetime


@versioned("planning", "request", 1)
class PlanningRequest(VersionedPayload):
    schema_version: Literal[1] = 1
    day: date
    timezone: str = Field(min_length=1, max_length=64)  # IANA name
    now: AwareDatetime
    working_window: Interval | None
    free_blocks: list[Interval] = Field(max_length=48)
    max_items: Literal[5] = MAX_PICKS
    candidates: list[PlanCandidate] = Field(max_length=MAX_CANDIDATES)
    projects: list[PlanProject] = Field(max_length=200)
    agents: list[ProjectAgentEntry] = Field(max_length=200)
    events: list[EventSummary] = Field(max_length=100)


class PlanPick(_Part):
    task_id: UUID
    reason: str = Field(min_length=3, max_length=140)


@versioned("planning", "result", 1)
class PlanningResult(VersionedPayload):
    """The `plan` skill's reply: at most five picks, in order, each from the candidates."""

    schema_version: Literal[1] = 1
    picks: list[PlanPick] = Field(max_length=MAX_PICKS)
    alternates: list[UUID] = Field(default=[], max_length=MAX_PICKS)
    notes: str | None = Field(default=None, max_length=300)
