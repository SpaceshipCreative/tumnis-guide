"""YAML seed loader with a pluggable sink (P0-02).

A seed set is one YAML file or a folder of them; each document carries `schema_version: 1`
and any of the sections `workspace`, `users`, `projects` (tasks nested, subtasks under
tasks), `day` + `events`, `documents`. Dates are offsets from an anchor day (`+21d`,
`-3d`) and event times are wall-clock times in the workspace timezone, so the set is the
same whichever day it loads. Records reach a `SeedSink`: `InMemorySink` for tests,
`DatabaseSink` for `tumnis seed`, which writes through each module's api as the entity
writers register (projects P0-17, tasks P0-18, events P0-12, documents P0-17).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from tumnis.core.clock import Clock, local_to_utc

SCHEMA_VERSION = 1

# The seed and load sets live beside the package in the source tree (backend/fixtures).
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class SeedSet(StrEnum):
    seed = "seed"
    load = "load"


SEED_PATHS = {SeedSet.seed: FIXTURES / "seed", SeedSet.load: FIXTURES / "load" / "load.yaml"}
DayOffset = Annotated[str, StringConstraints(pattern=r"^[+-]\d+d$")]
LocalTime = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
TaskStatus = Literal["backlog", "today", "in_progress", "waiting_on_human", "in_review", "done"]
TaskLabel = Literal["human", "ai", "hybrid"]


# --- Records handed to a sink (dates resolved against the anchor) ----------------------


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str


class WorkspaceSeed(_Record):
    name: str
    timezone: str


class UserSeed(_Record):
    email: str
    password: str
    totp_secret: str


class ProjectSeed(_Record):
    name: str
    client: str | None = None
    goal: str | None = None
    deadline: date | None = None
    sort_key: str | None = None  # board order (core/rank.py keys, P0-17)


class TaskSeed(_Record):
    title: str
    label: TaskLabel | None  # None: label pending (R-08)
    status: TaskStatus
    priority: int = 0
    estimate_minutes: int | None = None
    due_on: date | None = None
    first_action: str | None = None
    completed_at: datetime | None = None


class EventSeed(_Record):
    title: str
    start_at: datetime
    end_at: datetime
    busy: bool = True


class DocumentSeed(_Record):
    title: str
    kind: str = "text"
    role: str | None = None
    body: str


# --- YAML documents (extra="forbid", so a typo fails the load) --------------------------


class _Yaml(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _TaskYaml(_Yaml):
    key: str
    title: str
    label: TaskLabel | None = None
    status: TaskStatus = "backlog"
    priority: int = 0
    estimate_minutes: int | None = Field(default=None, ge=0)
    due: DayOffset | None = None
    first_action: str | None = None
    subtasks: list[_TaskYaml] = []


class _ProjectYaml(_Yaml):
    key: str
    name: str
    client: str | None = None
    goal: str | None = None
    deadline: DayOffset | None = None
    sort_key: str | None = None
    tasks: list[_TaskYaml] = []


class _EventYaml(_Yaml):
    key: str
    title: str
    start: LocalTime
    end: LocalTime
    busy: bool = True


class _DocumentYaml(_Yaml):
    key: str
    project: str | None = None
    title: str
    kind: str = "text"
    role: str | None = None
    body: str


class _SeedDocument(_Yaml):
    schema_version: Literal[1]
    workspace: WorkspaceSeed | None = None
    users: list[UserSeed] = []
    projects: list[_ProjectYaml] = []
    day: DayOffset | None = None
    events: list[_EventYaml] = []
    documents: list[_DocumentYaml] = []

    @model_validator(mode="after")
    def _events_need_a_day(self) -> _SeedDocument:
        if self.events and self.day is None:
            raise ValueError("events need a `day` offset in the same document")
        return self


# --- Sinks ------------------------------------------------------------------------------


class SeedSink(Protocol):
    async def workspace(self, rec: WorkspaceSeed) -> UUID: ...
    async def user(self, ws: UUID, rec: UserSeed) -> UUID: ...
    async def project(self, ws: UUID, rec: ProjectSeed) -> UUID: ...
    async def task(self, ws: UUID, project: UUID, parent: UUID | None, rec: TaskSeed) -> UUID: ...
    async def event(self, ws: UUID, rec: EventSeed) -> UUID: ...
    async def document(self, ws: UUID, project: UUID | None, rec: DocumentSeed) -> UUID: ...


@dataclass(frozen=True)
class Stored:
    kind: str
    id: UUID
    parents: dict[str, UUID | None]
    rec: _Record


@dataclass
class InMemorySink:
    """Keeps every record in load order, with the ids of its parents."""

    records: list[Stored] = field(default_factory=list)

    def _add(self, kind: str, rec: _Record, **parents: UUID | None) -> UUID:
        new_id = uuid.uuid4()
        self.records.append(Stored(kind, new_id, parents, rec))
        return new_id

    async def workspace(self, rec: WorkspaceSeed) -> UUID:
        return self._add("workspace", rec)

    async def user(self, ws: UUID, rec: UserSeed) -> UUID:
        return self._add("user", rec, workspace=ws)

    async def project(self, ws: UUID, rec: ProjectSeed) -> UUID:
        return self._add("project", rec, workspace=ws)

    async def task(self, ws: UUID, project: UUID, parent: UUID | None, rec: TaskSeed) -> UUID:
        return self._add("task", rec, workspace=ws, project=project, parent=parent)

    async def event(self, ws: UUID, rec: EventSeed) -> UUID:
        return self._add("event", rec, workspace=ws)

    async def document(self, ws: UUID, project: UUID | None, rec: DocumentSeed) -> UUID:
        return self._add("document", rec, workspace=ws, project=project)


SeedWriter = Callable[..., Awaitable[UUID]]
_WRITERS: dict[str, SeedWriter] = {}


class SeedWriterMissingError(LookupError):
    """No module has registered a database writer for this record kind yet."""


class SeedWriterUnavailableError(RuntimeError):
    """A writer exists but cannot store its record in this deployment (for example no
    master key to seal a secret with); `skip_missing` sinks skip the record."""


def writers_registered() -> bool:
    """False until the first module registers a writer (projects P0-17); a seed load before
    then has nothing to write to."""
    return bool(_WRITERS)


def register_seed_writer(kind: str, writer: SeedWriter) -> None:
    """Modules register the api call that stores one seed record of `kind`; the writer
    takes the same arguments as the matching `SeedSink` method."""
    _WRITERS[kind] = writer


class DatabaseSink:
    """Writes each record through the owning module's api (writers register as modules land).

    `skip_missing=True` (the CLI and `POST /v1/test/reset`) loads what the modules that have
    landed can store and skips record kinds with no writer yet (or whose writer cannot run
    here, SeedWriterUnavailableError), naming them in `skipped`
    (their ids are placeholders nothing is stored under); the default raises
    SeedWriterMissingError."""

    def __init__(self, *, skip_missing: bool = False) -> None:
        self.skip_missing = skip_missing
        self.skipped: set[str] = set()

    async def _write(self, kind: str, *args: Any) -> UUID:
        writer = _WRITERS.get(kind)
        if writer is None:
            if self.skip_missing:
                self.skipped.add(kind)
                return uuid.uuid4()
            raise SeedWriterMissingError(f"no seed writer registered for {kind!r}")
        try:
            return await writer(*args)
        except SeedWriterUnavailableError:
            if not self.skip_missing:
                raise
            self.skipped.add(kind)
            return uuid.uuid4()

    async def workspace(self, rec: WorkspaceSeed) -> UUID:
        return await self._write("workspace", rec)

    async def user(self, ws: UUID, rec: UserSeed) -> UUID:
        return await self._write("user", ws, rec)

    async def project(self, ws: UUID, rec: ProjectSeed) -> UUID:
        return await self._write("project", ws, rec)

    async def task(self, ws: UUID, project: UUID, parent: UUID | None, rec: TaskSeed) -> UUID:
        return await self._write("task", ws, project, parent, rec)

    async def event(self, ws: UUID, rec: EventSeed) -> UUID:
        return await self._write("event", ws, rec)

    async def document(self, ws: UUID, project: UUID | None, rec: DocumentSeed) -> UUID:
        return await self._write("document", ws, project, rec)


# --- Loader -----------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedResult:
    counts: dict[str, int]
    ids: dict[str, UUID]  # seed key -> id the sink minted


def read_seed(path: Path) -> list[_SeedDocument]:
    """Parse and validate a seed file, or every *.yaml in a folder (sorted by name) but
    the `expected_*.yaml` answer files beside them (P0-17's expected health)."""
    if path.is_dir():
        files = sorted(p for p in path.glob("*.yaml") if not p.name.startswith("expected_"))
    else:
        files = [path]
    if not files:
        raise FileNotFoundError(f"no seed files in {path}")
    return [
        _SeedDocument.model_validate(doc)
        for file in files
        for doc in yaml.safe_load_all(file.read_text())
        if doc is not None
    ]


def _day(anchor: date, offset: str | None) -> date | None:
    return None if offset is None else anchor + timedelta(days=int(offset[:-1]))


def _time(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


async def load_seed(
    path: Path, sink: SeedSink, *, anchor: date | None = None, clock: Clock
) -> SeedResult:
    """Load a seed set into `sink`. `anchor` defaults to today in the workspace timezone."""
    docs = read_seed(path)
    workspaces = [doc.workspace for doc in docs if doc.workspace is not None]
    if len(workspaces) != 1:
        raise ValueError(f"a seed set has exactly one workspace, found {len(workspaces)}")
    workspace = workspaces[0]
    tz = ZoneInfo(workspace.timezone)
    day0 = anchor or clock.now().astimezone(tz).date()
    ids: dict[str, UUID] = {}
    counts = dict.fromkeys(("workspace", "user", "project", "task", "event", "document"), 0)

    def remember(kind: str, key: str, new_id: UUID) -> None:
        if key in ids:
            raise ValueError(f"duplicate seed key {key!r}")
        ids[key] = new_id
        counts[kind] += 1

    ws = await sink.workspace(workspace)
    remember("workspace", workspace.key, ws)
    for doc in docs:
        for user in doc.users:
            remember("user", user.key, await sink.user(ws, user))

    async def add_tasks(project: UUID, parent: UUID | None, tasks: Iterable[_TaskYaml]) -> None:
        for task in tasks:
            rec = TaskSeed(
                key=task.key,
                title=task.title,
                label=task.label,
                status=task.status,
                priority=task.priority,
                estimate_minutes=task.estimate_minutes,
                due_on=_day(day0, task.due),
                first_action=task.first_action,
                completed_at=clock.now() if task.status == "done" else None,
            )
            task_id = await sink.task(ws, project, parent, rec)
            remember("task", task.key, task_id)
            await add_tasks(project, task_id, task.subtasks)

    for doc in docs:
        for project in doc.projects:
            project_rec = ProjectSeed(
                key=project.key,
                name=project.name,
                client=project.client,
                goal=project.goal,
                deadline=_day(day0, project.deadline),
                sort_key=project.sort_key,
            )
            project_id = await sink.project(ws, project_rec)
            remember("project", project.key, project_id)
            await add_tasks(project_id, None, project.tasks)

    for doc in docs:
        on = _day(day0, doc.day)
        for event in doc.events:
            assert on is not None  # guaranteed by _events_need_a_day  # noqa: S101
            event_rec = EventSeed(
                key=event.key,
                title=event.title,
                start_at=local_to_utc(on, _time(event.start), tz),
                end_at=local_to_utc(on, _time(event.end), tz),
                busy=event.busy,
            )
            remember("event", event.key, await sink.event(ws, event_rec))

    for doc in docs:
        for document in doc.documents:
            doc_project: UUID | None = None
            if document.project is not None:
                if document.project not in ids:
                    raise ValueError(f"{document.key}: unknown project {document.project!r}")
                doc_project = ids[document.project]
            document_rec = DocumentSeed(
                key=document.key,
                title=document.title,
                kind=document.kind,
                role=document.role,
                body=document.body,
            )
            remember("document", document.key, await sink.document(ws, doc_project, document_rec))

    return SeedResult(counts=counts, ids=ids)
