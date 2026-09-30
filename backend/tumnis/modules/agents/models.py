"""agents SQLAlchemy tables owned by this module (mirrors of revisions agents_0001, P1-04,
and agents_0002, P1-06).

- runners: one per runner daemon; its device token lives in auth's `device_tokens`.
- agent_profiles: the Hermes profiles Tumnis may run (one master, one per project).
- runs: every agent run; `kind` and `status` hold the whole R-22 vocabulary.
- run_events: what happened in a run, unique per (run, message) so a replayed message
  lands once.
- runner_messages: the mailbox between the worker and a runner's socket, both ways;
  `message_id` is unique, so a replayed step or frame is written once.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, text
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


class RunEventRow(TenantBase, Base):
    __tablename__ = "run_events"

    run_id: Mapped[UUID]
    message_id: Mapped[UUID]
    kind: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


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
