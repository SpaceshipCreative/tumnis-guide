"""projects event payload models (P0-17, A8, A12) and the archive subscribers (P2-18).

- `project.created`: a new project, emitted with its row. It carries the brief
  (`brief_md`, capped at 64 KB, plan default) for knowledge to store as the pinned first
  document, so projects never imports knowledge, and the agent choice (`profile`: create
  or link, P1-06) for agents to provision.
- `project.updated`: fields changed (`changed_fields`), including `archived_at` on
  unarchive and `sort_key` on reorder.
- `project.archived`: archived; data kept (P2-18: then compressed by `archive_project`).
- `project.purged`: an archived project purged for good (P2-18, R-37): the modules drop
  what its archive kept.
- `policy.changed`: the approval policy's `before` and `after` (the policy editor is
  P2-08; the payload is fixed here for the subscribers that react to it).

Subscribers (P2-18, FR-5.10): `projects.start_archive` starts `archive_project` on
`project.archived`, `projects.start_unarchive` starts `unarchive_project` on the
`project.updated` of an unarchive (`archived_at` changed, no longer archived), and
`projects.start_purge` starts `purge_project_archive` on `project.purged`. Each
workflow id comes from the event id, so a redelivered event starts nothing new. The
workflows module is imported when a subscriber runs: api imports this module for its
payloads, and workflows imports api.

`project.created` and `project.updated` also carry what search indexes (P0-20, additive,
since search may not call projects): the name and goal, and on updates whether the project
is archived.
"""

import importlib
from typing import Annotated, Any, ClassVar, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

from tumnis.core.events import EventEnvelope, EventPayload, event_type, subscribe

BRIEF_MAX_CHARS: Final = 65_536  # plan default: 64 KB
# A Hermes profile name (agents' NAME_RE, repeated here: projects cannot import agents).
PROFILE_NAME_PATTERN: Final = r"^[a-z0-9][a-z0-9-]{0,62}$"


class PolicyDoc(BaseModel):
    gated: list[str]
    allowed: list[str]
    tool_allowlist: list[str]
    max_concurrent_runs: int
    max_run_minutes: int
    max_tasks_per_run: int


class AgentProfileChoice(BaseModel):
    """The project's agent (P1-06): a new Hermes profile from the project template
    (`create`, the default; named after the project, so `name` is ignored), or an existing
    profile on the agent server, by `name` (`link`; `create_project` answers 422
    `invalid_profile_choice` without one). The agents module provisions it on
    `project.created`."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["create", "link"] = "create"
    name: Annotated[str, StringConstraints(pattern=PROFILE_NAME_PATTERN)] | None = None


@event_type("project.created", 1)
class ProjectCreatedV1(EventPayload):
    event_name: ClassVar[str] = "project.created"
    schema_version: Literal[1] = 1
    project_id: UUID
    name: str
    brief_md: Annotated[str, StringConstraints(max_length=BRIEF_MAX_CHARS)] = ""
    goal: str | None = None  # P0-20
    profile: AgentProfileChoice = AgentProfileChoice()  # P1-06: the agent to provision


@event_type("project.updated", 1)
class ProjectUpdatedV1(EventPayload):
    event_name: ClassVar[str] = "project.updated"
    schema_version: Literal[1] = 1
    project_id: UUID
    changed_fields: list[str]
    name: str | None = None  # P0-20: the project after the change
    goal: str | None = None
    archived: bool | None = None


@event_type("project.archived", 1)
class ProjectArchivedV1(EventPayload):
    event_name: ClassVar[str] = "project.archived"
    schema_version: Literal[1] = 1
    project_id: UUID


@event_type("policy.changed", 1)
class PolicyChangedV1(EventPayload):
    event_name: ClassVar[str] = "policy.changed"
    schema_version: Literal[1] = 1
    project_id: UUID
    before: PolicyDoc | None
    after: PolicyDoc


@event_type("project.purged", 1)
class ProjectPurgedV1(EventPayload):
    event_name: ClassVar[str] = "project.purged"
    schema_version: Literal[1] = 1
    project_id: UUID


# --- archive subscribers (P2-18) -----------------------------------------------------------


def _workflows() -> Any:
    return importlib.import_module("tumnis.modules.projects.workflows")


@subscribe("project.archived", name="projects.start_archive")
async def start_archive(envelope: EventEnvelope) -> None:
    await _workflows().start_archive(envelope)


@subscribe("project.updated", name="projects.start_unarchive")
async def start_unarchive(envelope: EventEnvelope) -> None:
    payload = envelope.payload
    if "archived_at" in payload.get("changed_fields", []) and payload.get("archived") is False:
        await _workflows().start_unarchive(envelope)


@subscribe("project.purged", name="projects.start_purge")
async def start_purge(envelope: EventEnvelope) -> None:
    await _workflows().start_purge(envelope)
