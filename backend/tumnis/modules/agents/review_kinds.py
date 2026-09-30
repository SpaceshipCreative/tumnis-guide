"""Review kinds the agents module queues (R-03), registered with P0-18's registry at import.

- `drift` (P2-10, SAF-2, SAF-3): a profile has an MCP server its project's allowlist does
  not name, or one of its tokens reaches another project's repo or app. Degraded profiles
  still run tasks; the item tells Scott what to fix in the profile (the app never writes
  into a profile's home, FR-5.12). Actions: `accept` (acknowledge) and `snooze`. The
  dedupe key hashes the finding, so the same drift on the next check queues nothing new.
"""

from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from tumnis.modules.tasks import api as tasks


class ForeignReach(BaseModel):
    """A token reaching another project's repo (`github`) or app (`coolify`)."""

    kind: Literal["github", "coolify"]
    target: str = Field(max_length=200)
    project_id: UUID | None = None  # the project that links it
    project_name: str | None = None


class DriftPayload(BaseModel):
    profile_id: UUID
    profile: str
    extra: list[str] = Field(default=[], max_length=200)  # servers outside the allowlist
    foreign: list[ForeignReach] = Field(default=[], max_length=100)
    fix: str = Field(max_length=2000)  # what to change in the profile, in words


DRIFT: Final = tasks.ReviewKindSpec(
    kind="drift",
    owner_module="agents",
    payload_schema=DriftPayload,
    actions=("accept", "snooze"),
    impact_scope="project",
)
tasks.register_review_kind(DRIFT)
