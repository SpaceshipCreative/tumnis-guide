"""projects public functions and DTOs; the only file other modules may import (P0-17).

A workspace creates, edits, archives, unarchives and reorders projects; each has its links,
one approval policy (FR-5.6 defaults, SAF-5 limits) and a brief, which travels to knowledge
in `project.created`. Every write runs in the caller's transaction (`s`), emits its event
there and marks the project changed for live clients.

Health needs task facts, but tasks imports projects, so projects cannot import tasks: the
tasks module registers a `ProjectStatsSource` at import (P0-18). Until then every project
counts zero tasks and is on track. Reads ask the source once per page for every id on it.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Annotated, Any, Final, Literal, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel, Field, StringConstraints
from pydantic.json_schema import SkipJsonSchema
from sqlalchemy import Table, delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import rank, tenancy
from tumnis.core.cache import CacheKey, CacheSpec, invalidate_on_commit, register_cache
from tumnis.core.clock import SystemClock
from tumnis.core.errors import ProblemError
from tumnis.core.limits import MAX_ESTIMATE_MINUTES
from tumnis.core.live import mark_changed
from tumnis.core.outbox import emit
from tumnis.core.pagination import Page, SortKey, paginate
from tumnis.core.tenancy import WorkspaceContext, session_for, tenant_session
from tumnis.core.types import SYSTEM_ACTOR, ActorRef
from tumnis.core.versioning import NotFound, StaleVersion, Version, update_versioned
from tumnis.modules.auth import api as auth
from tumnis.modules.projects.events import (
    BRIEF_MAX_CHARS,
    AgentProfileChoice,
    ProjectArchivedV1,
    ProjectCreatedV1,
    ProjectUpdatedV1,
)
from tumnis.modules.projects.models import Project, ProjectLink, ProjectPolicy
from tumnis.modules.projects.rules import (
    ALLOWED_DEFAULT,
    GATED_DEFAULT,
    MAX_CONCURRENT_RUNS_DEFAULT,
    MAX_RUN_MINUTES_DEFAULT,
    MAX_TASKS_PER_RUN_DEFAULT,
    CodeLocationError,
    Health,
    HealthFacts,
    local_today,
    next_milestone,
    project_health,
    validate_code_location,
)
from tumnis.seed import ProjectSeed, register_seed_writer

__all__ = ["AgentProfileChoice"]  # re-exported: the create body's agent choice (P1-06)

_log = structlog.get_logger(__name__)

_projects: Table = Project.__table__  # type: ignore[assignment]
_links: Table = ProjectLink.__table__  # type: ignore[assignment]
_policies: Table = ProjectPolicy.__table__  # type: ignore[assignment]

LIVE_ENTITY: Final = "project"
LinkKind = Literal["person", "domain", "repo", "coolify_app"]
ProjectStatus = Literal["active", "on_hold", "completed"]
Name = Annotated[str, StringConstraints(min_length=1, max_length=120, strip_whitespace=True)]
Goal = Annotated[str, StringConstraints(max_length=280)]
Brief = Annotated[str, StringConstraints(max_length=BRIEF_MAX_CHARS)]
# The subtask card threshold (FR-3.8), minutes; None: the workspace's (P0-18 lays out on it).
Threshold = Annotated[int, Field(ge=1, le=MAX_ESTIMATE_MINUTES)]


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
    code_path: str | None = None  # absolute POSIX path on the agent server, no ".." segments
    repo_url: str | None = None  # https:// or ssh git URL
    links: list[ProjectLinkIn] = []
    profile_name: str | None = None
    brief_md: Brief = ""  # carried to knowledge through project.created; reads return ""


class ProjectCreateIn(ProjectCreate):
    """The body of `POST /v1/projects`: a project and the agent to give it (P1-06), a new
    profile from the template (the default, also when `profile` is absent) or an existing
    one. Reads never carry it: the agent is the agents module's."""

    profile: AgentProfileChoice | None = None


class ProjectOut(ProjectCreate):
    id: UUID
    version: int
    sort_key: str
    archived_at: datetime | None
    health: Health
    open_count: int
    next_milestone: date | None
    last_agent_activity_at: datetime | None = None  # always None until P2-04
    subtask_threshold_min: int | None = None  # None: the workspace's threshold (FR-3.8)
    local_decisions_only: bool = False  # decisions never go to Jev (P1-02, Data flow rule 6)


class ProjectPatch(BaseModel):
    """Fields to change (absent: unchanged; null clears a nullable one) and the version
    read."""

    name: Name | None = None
    client: str | None = None
    goal: Goal | None = None
    deadline: date | None = None
    status: ProjectStatus | None = None
    code_path: str | None = None
    repo_url: str | None = None
    links: list[ProjectLinkIn] | None = None
    profile_name: str | None = None
    subtask_threshold_min: Threshold | None = None
    # optional, never null in the contract; an explicit null still reaches the 422 below
    local_decisions_only: bool | SkipJsonSchema[None] = None
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


# --- Task statistics (registered by tasks, P0-18) -----------------------------------------


class ProjectStatsSource(Protocol):
    async def stats(
        self, s: AsyncSession, project_ids: Sequence[UUID], today: date
    ) -> Mapping[UUID, ProjectStats]: ...


ZERO_STATS: Final = ProjectStats(
    health_facts=HealthFacts(waiting_on_human=0, overdue=0), open_count=0, next_open_due=None
)


class _NoTasks:
    """The default source: no tasks module yet, so every project counts zero tasks."""

    async def stats(
        self, s: AsyncSession, project_ids: Sequence[UUID], today: date
    ) -> Mapping[UUID, ProjectStats]:
        return dict.fromkeys(project_ids, ZERO_STATS)


_source: list[ProjectStatsSource] = [_NoTasks()]


def register_stats_source(src: ProjectStatsSource) -> None:
    """The tasks module registers its source at import (P0-18); the last one wins."""
    _source[0] = src


def stats_source() -> ProjectStatsSource:
    return _source[0]


# --- Reading -------------------------------------------------------------------------------


class _Row(BaseModel):
    id: UUID
    version: int
    name: str
    client: str | None
    goal: str | None
    deadline: date | None
    status: ProjectStatus
    sort_key: str
    code_path: str | None
    repo_url: str | None
    profile_name: str | None
    archived_at: datetime | None
    subtask_threshold_min: int | None
    local_decisions_only: bool = False


def _now(now: datetime | None) -> datetime:
    return now if now is not None else SystemClock().now()


def _context() -> WorkspaceContext:
    ctx = tenancy.current()
    if ctx is None:
        raise RuntimeError("projects api called outside a workspace context")
    return ctx


async def _today(now: datetime | None) -> date:
    """The workspace's local day (health judges overdue on it, REL-6)."""
    settings = await auth.get_workspace_settings(_context())
    return local_today(_now(now), ZoneInfo(settings.timezone))


async def _links_of(s: AsyncSession, ids: Sequence[UUID]) -> dict[UUID, list[ProjectLinkIn]]:
    found: dict[UUID, list[ProjectLinkIn]] = {pid: [] for pid in ids}
    if not ids:
        return found
    rows = await s.execute(
        select(_links.c.project_id, _links.c.kind, _links.c.value)
        .where(_links.c.project_id.in_(ids), _links.c.deleted_at.is_(None))
        .order_by(_links.c.id)
    )
    for row in rows:
        found[row.project_id].append(ProjectLinkIn(kind=row.kind, value=row.value))
    return found


async def _outs(s: AsyncSession, rows: Sequence[_Row], now: datetime | None) -> list[ProjectOut]:
    """Rows as ProjectOut: one links query and one stats call for them all."""
    ids = [row.id for row in rows]
    if not ids:
        return []
    today = await _today(now)
    stats = await stats_source().stats(s, ids, today)
    links = await _links_of(s, ids)
    out = []
    for row in rows:
        st = stats.get(row.id, ZERO_STATS)
        out.append(
            ProjectOut(
                **row.model_dump(),
                links=links[row.id],
                health=project_health(st.health_facts),
                open_count=st.open_count,
                next_milestone=next_milestone(row.deadline, st.next_open_due, today),
            )
        )
    return out


def _live() -> Any:
    return _projects.c.deleted_at.is_(None)


async def _row(s: AsyncSession, project_id: UUID, *, lock: bool = False) -> _Row:
    stmt = select(_projects).where(_projects.c.id == project_id, _live())
    if lock:
        stmt = stmt.with_for_update()
    found = (await s.execute(stmt)).mappings().first()
    if found is None:
        raise NotFound("projects", project_id)
    return _Row.model_validate(dict(found))


async def get_project(
    s: AsyncSession, project_id: UUID, *, now: datetime | None = None
) -> ProjectOut:
    """A project, archived or not; NotFound (404) otherwise."""
    [out] = await _outs(s, [await _row(s, project_id)], now)
    return out


async def list_projects(
    s: AsyncSession,
    *,
    include_archived: bool = False,
    cursor: str | None = None,
    limit: int = 50,
    project_ids: frozenset[UUID] | None = None,
    now: datetime | None = None,
) -> Page[ProjectOut]:
    """Projects in board order (`sort_key`, then id); archived ones only when asked.
    `project_ids` limits the list (a project-limited key, R-28)."""
    stmt = select(_projects).where(_live())
    if not include_archived:
        stmt = stmt.where(_projects.c.archived_at.is_(None))
    if project_ids is not None:
        stmt = stmt.where(_projects.c.id.in_(project_ids))
    page = await paginate(
        s,
        stmt,
        keys=[SortKey(_projects.c.sort_key)],
        id_col=_projects.c.id,
        cursor=cursor,
        limit=limit,
        model=_Row,
    )
    return Page[ProjectOut](items=await _outs(s, page.items, now), next_cursor=page.next_cursor)


async def get_policy(s: AsyncSession, project_id: UUID) -> PolicyOut:
    row = (
        (
            await s.execute(
                select(_policies).where(
                    _policies.c.project_id == project_id, _policies.c.deleted_at.is_(None)
                )
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("project_policies", project_id)
    return PolicyOut.model_validate(dict(row))


async def effective_subtask_threshold(s: AsyncSession, project_id: UUID) -> int:
    """The project's subtask threshold (minutes), else the workspace's (default 30)."""
    row = await _row(s, project_id)
    if row.subtask_threshold_min is not None:
        return row.subtask_threshold_min
    return (await auth.get_workspace_settings(_context())).subtask_threshold_min


class ProjectLinkOut(BaseModel):
    project_id: UUID
    value: str


async def links_of_kind(
    s: AsyncSession, kind: LinkKind, *, project_ids: frozenset[UUID] | None = None
) -> list[ProjectLinkOut]:
    """The links of one kind of the workspace's live, unarchived projects, by project
    board order then link order (P2-14: the Coolify applications to poll and show).
    `project_ids` limits them (a project-limited key, R-28)."""
    stmt = (
        select(_links.c.project_id, _links.c.value)
        .join(_projects, _projects.c.id == _links.c.project_id)
        .where(
            _links.c.kind == kind,
            _links.c.deleted_at.is_(None),
            _live(),
            _projects.c.archived_at.is_(None),
        )
        .order_by(_projects.c.sort_key, _projects.c.id, _links.c.id)
    )
    if project_ids is not None:
        stmt = stmt.where(_links.c.project_id.in_(project_ids))
    rows = await s.execute(stmt)
    return [ProjectLinkOut(project_id=row.project_id, value=row.value) for row in rows]


LOCAL_ONLY_CACHE: Final = register_cache(
    CacheSpec("projects.local_only", "workspace", None, ("update_project (local_decisions_only)",))
)


def _local_only_key(workspace_id: UUID, project_id: UUID) -> CacheKey:
    return CacheKey.for_workspace(workspace_id, "projects.local_only", str(project_id))


async def local_decisions_only(project_id: UUID, *, session: AsyncSession | None = None) -> bool:
    """Whether the project keeps its decisions local (P1-02, Data flow rule 6): the hot
    lookup every decision makes, cached until `update_project` changes the switch. False
    for a project that does not exist (or was deleted)."""
    ctx = _context()
    key = _local_only_key(ctx.workspace_id, project_id)
    cached = await LOCAL_ONLY_CACHE.get(key)
    if cached is not None:
        return cached == b"1"
    token = LOCAL_ONLY_CACHE.token()
    async with session_for(ctx, session) as s:
        value = await s.scalar(
            select(_projects.c.local_decisions_only).where(_projects.c.id == project_id, _live())
        )
    flag = bool(value)
    await LOCAL_ONLY_CACHE.fill(key, b"1" if flag else b"0", since=token)
    return flag


async def project_names(s: AsyncSession, project_ids: Sequence[UUID]) -> dict[UUID, str]:
    """The names of the live projects (archived or not) among `project_ids`."""
    if not project_ids:
        return {}
    rows = await s.execute(
        select(_projects.c.id, _projects.c.name).where(
            _projects.c.id.in_(list(project_ids)), _live()
        )
    )
    return {row.id: row.name for row in rows}


async def project_exists(s: AsyncSession, project_id: UUID) -> bool:
    """A live project (archived or not) of the caller's workspace."""
    found = await s.scalar(select(_projects.c.id).where(_projects.c.id == project_id, _live()))
    return found is not None


def check_code_location(code_path: str | None, repo_url: str | None) -> None:
    """The FR-2.1 code location rule for other modules (the task packet, P2-07): raises a
    ValueError with `code` (`code_location_conflict` for both, `invalid_code_location`)."""
    validate_code_location(code_path, repo_url)


# --- Writing -------------------------------------------------------------------------------


def _check_location(code_path: str | None, repo_url: str | None) -> None:
    try:
        validate_code_location(code_path, repo_url)
    except CodeLocationError as exc:
        raise ProblemError(422, exc.code, str(exc)) from None


def _name_taken(exc: IntegrityError) -> ProblemError:
    if "ux_projects_ws_name" in str(exc.orig):
        return ProblemError(409, "project_name_taken", "Another project has this name")
    raise exc


async def _last_key(s: AsyncSession) -> str | None:
    key: str | None = await s.scalar(
        select(_projects.c.sort_key)
        .where(_live())
        .order_by(_projects.c.sort_key.desc(), _projects.c.id.desc())
        .limit(1)
    )
    return key


async def _write_links(s: AsyncSession, project_id: UUID, links: Sequence[ProjectLinkIn]) -> None:
    if links:
        await s.execute(
            insert(_links),
            [{"project_id": project_id, "kind": link.kind, "value": link.value} for link in links],
        )


async def create_project(
    s: AsyncSession,
    actor: ActorRef,
    data: ProjectCreate,
    *,
    now: datetime | None = None,
    sort_key: str | None = None,
) -> ProjectOut:
    """Inserts the project (last in board order unless `sort_key` is given), its links
    and its default policy, and emits `project.created` carrying the brief, in the
    caller's transaction, with the agent to give the project (`ProjectCreateIn.profile`,
    P1-06). 422 `code_location_conflict` / `invalid_code_location` /
    `invalid_profile_choice` (a link without a name); 409 `project_name_taken`."""
    _check_location(data.code_path, data.repo_url)
    if sort_key is None:
        sort_key = rank.between(await _last_key(s), None)
    values = data.model_dump(exclude={"schema_version", "links", "brief_md", "profile"})
    profile = getattr(data, "profile", None) or AgentProfileChoice()
    if profile.mode == "link" and profile.name is None:
        raise ProblemError(
            422, "invalid_profile_choice", "Linking an existing profile needs its name"
        )
    if profile.mode == "create":
        profile = AgentProfileChoice()  # a new profile is named after the project
    try:
        created = (
            (
                await s.execute(
                    insert(_projects)
                    .values(**values, sort_key=sort_key, created_by=actor)
                    .returning(*_projects.c)
                )
            )
            .mappings()
            .one()
        )
    except IntegrityError as exc:
        raise _name_taken(exc) from None
    project_id: UUID = created["id"]
    await _write_links(s, project_id, data.links)
    await s.execute(
        insert(_policies).values(
            project_id=project_id,
            gated=list(GATED_DEFAULT),
            allowed=list(ALLOWED_DEFAULT),
            tool_allowlist=[],
            max_concurrent_runs=MAX_CONCURRENT_RUNS_DEFAULT,
            max_run_minutes=MAX_RUN_MINUTES_DEFAULT,
            max_tasks_per_run=MAX_TASKS_PER_RUN_DEFAULT,
            created_by=actor,
        )
    )
    await emit(
        s,
        ProjectCreatedV1(
            project_id=project_id,
            name=data.name,
            brief_md=data.brief_md,
            goal=data.goal,
            profile=profile,
        ),
        occurred_at=_now(now),
    )
    mark_changed(s, LIVE_ENTITY, project_id)
    [out] = await _outs(s, [_Row.model_validate(dict(created))], now)
    return out


async def _versioned(
    s: AsyncSession, project_id: UUID, version: int, values: Mapping[str, Any], now: datetime | None
) -> _Row:
    """update_versioned on projects; a stale version answers 409 with the project as it
    is now (`current`)."""
    try:
        row = await update_versioned(s, _projects, project_id, version, values)
    except StaleVersion:
        current = await get_project(s, project_id, now=now)
        raise StaleVersion(current=current.model_dump(mode="json")) from None
    except IntegrityError as exc:
        raise _name_taken(exc) from None
    return _Row.model_validate(dict(row))


async def _changed(
    s: AsyncSession, row: _Row, fields: Sequence[str], now: datetime | None
) -> ProjectOut:
    await emit(
        s,
        ProjectUpdatedV1(
            project_id=row.id,
            changed_fields=sorted(fields),
            name=row.name,
            goal=row.goal,
            archived=row.archived_at is not None,
        ),
        occurred_at=_now(now),
    )
    mark_changed(s, LIVE_ENTITY, row.id)
    [out] = await _outs(s, [row], now)
    return out


async def update_project(
    s: AsyncSession,
    actor: ActorRef,
    project_id: UUID,
    patch: ProjectPatch,
    version: int,
    *,
    now: datetime | None = None,
) -> ProjectOut:
    """Changes the fields the patch sets (links are replaced as a whole) at `version`;
    emits `project.updated` with the changed field names."""
    current = await _row(s, project_id)  # 404 before any body rule (A0.3, #28)
    values = patch.model_dump(exclude_unset=True, exclude={"version", "links"})
    for required in ("name", "status", "local_decisions_only"):
        if required in values and values[required] is None:
            raise ProblemError(422, "validation_error", f"{required} cannot be null")
    if "code_path" in values or "repo_url" in values:
        _check_location(
            values.get("code_path", current.code_path), values.get("repo_url", current.repo_url)
        )
    fields = list(values)
    if patch.links is not None:
        fields.append("links")
    row = await _versioned(s, project_id, version, values or {"updated_at": func.now()}, now)
    if "local_decisions_only" in values:
        await invalidate_on_commit(s, _local_only_key(_context().workspace_id, project_id))
    if patch.links is not None:
        await s.execute(delete(_links).where(_links.c.project_id == project_id))
        await _write_links(s, project_id, patch.links)
    return await _changed(s, row, fields, now)


async def archive_project(
    s: AsyncSession,
    actor: ActorRef,
    project_id: UUID,
    version: int,
    *,
    now: datetime | None = None,
) -> ProjectOut:
    """Hides the project from lists; its data stays. Emits `project.archived`."""
    row = await _versioned(s, project_id, version, {"archived_at": _now(now)}, now)
    await emit(s, ProjectArchivedV1(project_id=project_id), occurred_at=_now(now))
    mark_changed(s, LIVE_ENTITY, project_id)
    [out] = await _outs(s, [row], now)
    return out


async def unarchive_project(
    s: AsyncSession,
    actor: ActorRef,
    project_id: UUID,
    version: int,
    *,
    now: datetime | None = None,
) -> ProjectOut:
    """Back in the lists at its old place (its sort key was kept)."""
    row = await _versioned(s, project_id, version, {"archived_at": None}, now)
    return await _changed(s, row, ["archived_at"], now)


async def _neighbour_key(s: AsyncSession, neighbour: UUID | None, moving: UUID) -> str | None:
    if neighbour is None:
        return None
    if neighbour == moving:
        raise ProblemError(422, "invalid_rank", "A project cannot move next to itself")
    key: str | None = await s.scalar(
        select(_projects.c.sort_key).where(_projects.c.id == neighbour, _live()).with_for_update()
    )
    if key is None:
        raise NotFound("projects", neighbour)
    return key


async def _rebalance(
    s: AsyncSession,
    project_id: UUID,
    *,
    after_id: UUID | None,
    before_id: UUID | None,
    version: int,
    now: datetime | None,
) -> _Row:
    """Rewrites every live project's key with `n_keys(n)` in board order, the moving one
    placed after `after_id` (or before `before_id`); logged as `rank.rebalanced`."""
    rows = (
        await s.execute(
            select(_projects.c.id, _projects.c.sort_key)
            .where(_live())
            .order_by(_projects.c.sort_key, _projects.c.id)
            .with_for_update()
        )
    ).all()
    order = [row.id for row in rows if row.id != project_id]
    if after_id is not None:
        order.insert(order.index(after_id) + 1, project_id)
    elif before_id is not None:
        order.insert(order.index(before_id), project_id)
    else:  # pragma: no cover  # between(None, None) is short: no rebalance
        order.append(project_id)
    old = {row.id: row.sort_key for row in rows}
    moved: _Row | None = None
    for pid, key in zip(order, rank.n_keys(len(order)), strict=True):
        if pid == project_id:
            moved = await _versioned(s, pid, version, {"sort_key": key}, now)
        elif old[pid] != key:
            await s.execute(update(_projects).where(_projects.c.id == pid).values(sort_key=key))
    _log.info("rank.rebalanced", table="projects", rows=len(order))
    assert moved is not None  # noqa: S101  # the moving project is in `order`
    return moved


async def reorder_project(
    s: AsyncSession,
    actor: ActorRef,
    project_id: UUID,
    *,
    after_id: UUID | None,
    before_id: UUID | None,
    version: int,
    now: datetime | None = None,
) -> ProjectOut:
    """Moves the project between two neighbours (None: an open end), writing one row: its
    key comes from the neighbours' keys, read `FOR UPDATE`. A key that would reach
    `MAX_KEY_LEN` rebalances every project instead (rare, logged, one transaction). 422
    `invalid_rank` when the neighbours are out of order."""
    after = await _neighbour_key(s, after_id, project_id)
    before = await _neighbour_key(s, before_id, project_id)
    try:
        key = rank.between(after, before)
    except rank.RankError as exc:
        raise ProblemError(422, exc.code, str(exc)) from None
    if len(key) >= rank.MAX_KEY_LEN:
        row = await _rebalance(
            s, project_id, after_id=after_id, before_id=before_id, version=version, now=now
        )
    else:
        row = await _versioned(s, project_id, version, {"sort_key": key}, now)
    return await _changed(s, row, ["sort_key"], now)


# --- Seed writer (P0-02's seed sets) ---------------------------------------------------------


async def seed_project(workspace_id: UUID, rec: ProjectSeed) -> UUID:
    """A seed project, made as the system actor through create_project."""
    data = ProjectCreate(name=rec.name, client=rec.client, goal=rec.goal, deadline=rec.deadline)
    async with tenant_session(WorkspaceContext(workspace_id, SYSTEM_ACTOR)) as s:
        out = await create_project(s, SYSTEM_ACTOR, data, sort_key=rec.sort_key)
    return out.id


register_seed_writer("project", seed_project)
