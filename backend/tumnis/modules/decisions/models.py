"""decisions SQLAlchemy tables owned by this module (mirrors of revisions decisions_0001
to decisions_0004)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import LargeBinary, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tumnis.core.base import Base, TenantBase


class ProviderConfig(TenantBase, Base):
    """One AI slot's provider, fallback, pinned model and sealed credential (FR-11.1)."""

    __tablename__ = "provider_configs"

    slot: Mapped[str]
    primary: Mapped[str]
    fallback: Mapped[str | None]
    credentials_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    model_version: Mapped[str]


class Threshold(TenantBase, Base):
    """The routing threshold of one decision point for one pinned model (P1-02)."""

    __tablename__ = "thresholds"

    decision_point: Mapped[str]
    model_version: Mapped[str]
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(server_default="default")
    needs_recheck: Mapped[bool] = mapped_column(server_default=text("false"))


class DecisionLog(TenantBase, Base):
    """One row per decision: typed answers, never the input text (P1-02, FR-11.5)."""

    __tablename__ = "decision_log"

    decision_point: Mapped[str]
    project_id: Mapped[UUID | None]
    subject_type: Mapped[str]
    subject_id: Mapped[UUID]
    provider: Mapped[str]
    fallback: Mapped[bool] = mapped_column(server_default=text("false"))
    fallback_reason: Mapped[str | None]
    model_version: Mapped[str | None]
    input_hash: Mapped[bytes] = mapped_column(LargeBinary)
    fields_sent: Mapped[list[str]] = mapped_column(ARRAY(Text))
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    confidence: Mapped[float | None]
    threshold: Mapped[dict[str, Any]] = mapped_column(JSONB)
    outcome: Mapped[str]
    cached: Mapped[bool] = mapped_column(server_default=text("false"))
    latency_ms: Mapped[int | None]
    overridden: Mapped[bool | None]
    final_value: Mapped[Any | None] = mapped_column(JSONB)
    outcome_at: Mapped[datetime | None]


class ThresholdHistory(TenantBase, Base):
    """One human edit of a threshold, with the value before, the value after and the
    reason (P3-08, FR-11.5); `created_by` is the editor."""

    __tablename__ = "thresholds_history"

    decision_point: Mapped[str]
    model_version: Mapped[str]
    before: Mapped[dict[str, Any]] = mapped_column(JSONB)
    after: Mapped[dict[str, Any]] = mapped_column(JSONB)
    reason: Mapped[str]


class SpeechClip(TenantBase, Base):
    """A focus message spoken by the server engine (P4-03, FR-11.7): one WAV per message,
    served through the API until `expires_at` (`CLIP_TTL_MIN`)."""

    __tablename__ = "speech_clips"

    message_id: Mapped[UUID]  # the focus event's id; no foreign key: the focus module's row
    mime: Mapped[str]
    audio: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime]


class DecisionEval(TenantBase, Base):
    """One `tumnis decisions eval` result for one point, provider and model (P3-08)."""

    __tablename__ = "decision_evals"

    decision_point: Mapped[str]
    provider: Mapped[str]
    model_version: Mapped[str]
    set_sha256: Mapped[str]
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # JSON null under 100
    run_at: Mapped[datetime]
