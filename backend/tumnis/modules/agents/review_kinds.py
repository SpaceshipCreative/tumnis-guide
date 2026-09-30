"""Review kinds the agents module queues (R-03), registered with P0-18's registry at import.

- `drift` (P2-10, SAF-2, SAF-3): a profile has an MCP server its project's allowlist does
  not name, or one of its tokens reaches another project's repo or app. Degraded profiles
  still run tasks; the item tells Scott what to fix in the profile (the app never writes
  into a profile's home, FR-5.12). Actions: `accept` (acknowledge) and `snooze`. The
  dedupe key hashes the finding, so the same drift on the next check queues nothing new.
- `result` (P2-04, FR-5.8): an agent's result for its task (summary, files touched,
  links). Accept moves the task to Done; reject needs `{feedback}`, which becomes a comment
  on the task, returns it to In progress and runs the agent again (`request_run` with
  `rerun_of`). `agents.apply_review_decision` applies both.
- `run_limit` (P2-04, SAF-5): a run stopped at its active-time cap or the wall-clock
  ceiling; the task stays In progress with the log. Accept acknowledges it.
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


# --- Runs (P2-04, FR-5.8, SAF-5) --------------------------------------------------------------

RESULT: Final = "result"
RUN_LIMIT: Final = "run_limit"


class ResultPayload(tasks.ResultFields):
    """An agent's result waiting for the human: accept finishes the task, reject sends it
    back to the agent with the feedback (a comment on the task and a new run)."""

    run_id: UUID


class RejectFeedback(BaseModel):
    feedback: str = Field(min_length=1, max_length=8000, pattern=r"\S")


class RunLimitPayload(BaseModel):
    """A run stopped at a time limit (the active-time cap or the wall-clock ceiling); the
    task stays In progress with the log. Accept acknowledges it."""

    run_id: UUID
    task_id: UUID | None = None
    reason: str = Field(max_length=100)


tasks.register_review_kind(
    tasks.ReviewKindSpec(
        kind=RESULT,
        owner_module="agents",
        payload_schema=ResultPayload,
        actions=("accept", "reject", "snooze"),
        impact_scope="task",
        action_payloads={"reject": RejectFeedback},
    )
)
tasks.register_review_kind(
    tasks.ReviewKindSpec(
        kind=RUN_LIMIT,
        owner_module="agents",
        payload_schema=RunLimitPayload,
        actions=("accept", "snooze"),
        impact_scope="task",
    )
)
