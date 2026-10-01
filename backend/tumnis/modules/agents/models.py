"""agents SQLAlchemy tables owned by this module (mirrors of revisions agents_0001, P1-04,
agents_0002, P2-07, agents_0003, P1-06, agents_0004, P2-02, agents_0005, P2-04, agents_0006,
P2-05, and agents_0007, P2-09).

- runners: one per runner daemon; its device token lives in auth's `device_tokens`.
- agent_profiles: the Hermes profiles Tumnis may run (one master, one per project);
  `api_key_id` is the key its runs' task tokens are issued from (P2-02).
- runs: every agent run; `kind` and `status` hold the whole R-22 vocabulary. P2-04 adds
  the state machine's bookkeeping (`state_seq`, budgets, dispatch packet, rerun link) and
  at most one active run of a kind per task.
- run_events: what happened in a run, unique per (run, message) so a replayed message
  lands once; `seq` (P2-04) orders a run's events.
- agent_pauses (P2-09, SAF-4): the kill switch. One open pause (not resumed) per
  workspace, or per project; while one holds a project, its runs are not dispatched.
- runner_messages: the mailbox between the worker and a runner's socket, both ways;
  `message_id` is unique, so a replayed step or frame is written once.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, text
from sqlalchemy.dialects.postgresql import BYTEA, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tumnis.core.base import Base, TenantBase


class Runner(TenantBase, Base):
    __tablename__ = "runners"

    name: Mapped[str]
    host: Mapped[str | None]
    os: Mapped[str | None]
    daemon_version: Mapped[str | None]
    hermes_version: Mapped[str | None]
    protocol_version: Mapped[int | None]
    last_heartbeat_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(server_default=text("'never_seen'"))
    inventory: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )


class AgentProfile(TenantBase, Base):
    __tablename__ = "agent_profiles"

    name: Mapped[str]
    role: Mapped[str]
    project_id: Mapped[UUID | None]
    runner_id: Mapped[UUID | None] = mapped_column(ForeignKey("runners.id"))
    transport: Mapped[str]
    endpoint: Mapped[str | None]
    credentials_enc: Mapped[bytes | None] = mapped_column(BYTEA)
    profile_version: Mapped[str | None]  # the plan's `version`; the base `version` is the row's
    capabilities: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(server_default=text("'registered'"))
    health: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    health_checked_at: Mapped[datetime | None]
    provision_mode: Mapped[str] = mapped_column(server_default=text("'create'"))  # P1-06
    provision_attempts: Mapped[int] = mapped_column(server_default=text("0"))  # P1-06
    api_key_id: Mapped[UUID | None]  # P2-02: auth's api_keys row (no cross-module FK)


class RunRow(TenantBase, Base):
    __tablename__ = "runs"

    task_id: Mapped[UUID | None]
    profile_id: Mapped[UUID]
    kind: Mapped[str]
    status: Mapped[str]
    workflow_id: Mapped[str | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    correlation_id: Mapped[str]
    tainted: Mapped[bool] = mapped_column(server_default=text("false"))
    delegation_id: Mapped[UUID | None]
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None]
    profile_version: Mapped[str | None]  # the profile's VERSION when the run started (P2-07)
    # P2-04 (agents_0005)
    state_seq: Mapped[int] = mapped_column(server_default=text("0"))  # bumped per transition
    active_seconds_used: Mapped[float] = mapped_column(server_default=text("0"))
    runner_id: Mapped[UUID | None]  # the runner it was dispatched to (no foreign key)
    stop_reason: Mapped[str | None]
    packet: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # its task token redacted
    rerun_of: Mapped[UUID | None]
    tasks_created: Mapped[int] = mapped_column(server_default=text("0"))


class RunEventRow(TenantBase, Base):
    __tablename__ = "run_events"

    run_id: Mapped[UUID]
    message_id: Mapped[UUID]
    kind: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # P2-04 (agents_0005): the run's event order; always the database's default
    seq: Mapped[int | None] = mapped_column(
        BigInteger, server_default=text("nextval('run_events_seq_seq')")
    )


class QuestionRow(TenantBase, Base):  # P2-05 (agents_0006)
    __tablename__ = "questions"

    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"))
    task_id: Mapped[UUID | None]
    review_item_id: Mapped[UUID | None]
    workflow_id: Mapped[str | None]
    decided_by: Mapped[str | None]
    decided_at: Mapped[datetime | None]
    prompt: Mapped[str]
    choices: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(server_default=text("'pending'"))
    answer: Mapped[str | None]


class ApprovalRow(TenantBase, Base):  # P2-05 (agents_0006)
    __tablename__ = "approvals"

    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"))
    task_id: Mapped[UUID | None]
    review_item_id: Mapped[UUID | None]
    workflow_id: Mapped[str | None]
    decided_by: Mapped[str | None]
    decided_at: Mapped[datetime | None]
    action_class: Mapped[str]
    description: Mapped[str] = mapped_column(server_default=text("''"))
    target: Mapped[str | None]
    rule: Mapped[str]
    status: Mapped[str] = mapped_column(server_default=text("'pending'"))
    reason: Mapped[str | None]


class RunnerMessage(TenantBase, Base):
    __tablename__ = "runner_messages"

    runner_id: Mapped[UUID] = mapped_column(ForeignKey("runners.id"))
    message_id: Mapped[UUID]
    direction: Mapped[str]
    type: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(server_default=text("'queued'"))
    sent_at: Mapped[datetime | None]
    acked_at: Mapped[datetime | None]


class AgentPause(TenantBase, Base):
    __tablename__ = "agent_pauses"

    scope: Mapped[str]  # "workspace" | "project"
    project_id: Mapped[UUID | None]  # the project's (no cross-module foreign key)
    paused_at: Mapped[datetime]
    paused_by: Mapped[str]  # the actor
    reason: Mapped[str]
    resumed_at: Mapped[datetime | None]
    resumed_by: Mapped[str | None]
    resume_reason: Mapped[str | None]
    tainted: Mapped[bool] = mapped_column(server_default=text("false"))  # a key's pause (R-31)
