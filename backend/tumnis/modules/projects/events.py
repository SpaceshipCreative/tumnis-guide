"""projects event payload models (P0-17, A8, A12). Projects subscribes to nothing.

- `project.created`: a new project, emitted with its row. It carries the brief
  (`brief_md`, capped at 64 KB, plan default) for knowledge to store as the pinned first
  document, so projects never imports knowledge, and the agent choice (`profile`: create
  or link, P1-06) for agents to provision.
- `project.updated`: fields changed (`changed_fields`), including `archived_at` on
  unarchive and `sort_key` on reorder.
- `project.archived`: archived; data kept.
- `policy.changed`: the approval policy's `before` and `after` (the policy editor is
  P2-08; the payload is fixed here for the subscribers that react to it).

`project.created` and `project.updated` also carry what search indexes (P0-20, additive,
since search may not call projects): the name and goal, and on updates whether the project
is archived.
"""

from typing import Annotated, ClassVar, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

from tumnis.core.events import EventPayload, event_type

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
