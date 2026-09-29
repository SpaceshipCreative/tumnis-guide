"""projects public functions and DTOs; the only file other modules may import
(interface stub, P0-17)."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.pagination import Page
from tumnis.core.types import ActorRef
from tumnis.core.versioning import Version
from tumnis.modules.projects.rules import Health, HealthFacts

LinkKind = Literal["person", "domain", "repo", "coolify_app"]
ProjectStatus = Literal["active", "on_hold", "completed"]
Name = Annotated[str, StringConstraints(min_length=1, max_length=120, strip_whitespace=True)]
Goal = Annotated[str, StringConstraints(max_length=280)]


class ProjectLinkIn(BaseModel):
    kind: LinkKind
    value: Annotated[str, StringConstraints(min_length=1, max_length=320, strip_whitespace=True)]


class ProjectCreate(BaseModel):
    schema_version: Literal[1] = 1
    name: Name
    client: str | None = None
    goal: Goal | None = None
    deadline: date | None = None
    status: ProjectStatus = "active"
    code_path: str | None = None
    repo_url: str | None = None
    links: list[ProjectLinkIn] = []
    profile_name: str | None = None
    brief_md: str = ""


class ProjectOut(ProjectCreate):
    id: UUID
    version: int
    sort_key: str
    archived_at: datetime | None
    health: Health
    open_count: int
    next_milestone: date | None
    last_agent_activity_at: datetime | None = None


class ProjectPatch(BaseModel):
    name: Name | None = None
    client: str | None = None
    goal: Goal | None = None
    deadline: date | None = None
    status: ProjectStatus | None = None
    code_path: str | None = None
    repo_url: str | None = None
    links: list[ProjectLinkIn] | None = None
    profile_name: str | None = None
    version: Version


class ProjectStats(BaseModel):
    health_facts: HealthFacts
    open_count: int
    next_open_due: date | None


class PolicyOut(BaseModel):
    project_id: UUID
    gated: list[str]
    allowed: list[str]
    tool_allowlist: list[str]
    max_concurrent_runs: int
    max_run_minutes: int
    max_tasks_per_run: int
    version: int


class ProjectStatsSource(Protocol):
    async def stats(
        self, s: AsyncSession, project_ids: Sequence[UUID], today: date
    ) -> Mapping[UUID, ProjectStats]: ...


def register_stats_source(src: ProjectStatsSource) -> None:
    raise NotImplementedError


def stats_source() -> ProjectStatsSource:
    raise NotImplementedError


async def create_project(
    s: AsyncSession, actor: ActorRef, data: ProjectCreate, *, now: datetime | None = None
) -> ProjectOut:
    raise NotImplementedError


async def get_project(
    s: AsyncSession, project_id: UUID, *, now: datetime | None = None
) -> ProjectOut:
    raise NotImplementedError


async def list_projects(
    s: AsyncSession,
    *,
    include_archived: bool = False,
    cursor: str | None = None,
    limit: int = 50,
    project_ids: frozenset[UUID] | None = None,
    now: datetime | None = None,
) -> Page[ProjectOut]:
    raise NotImplementedError


async def get_policy(s: AsyncSession, project_id: UUID) -> PolicyOut:
    raise NotImplementedError
