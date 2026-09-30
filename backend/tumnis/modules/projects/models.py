"""projects SQLAlchemy tables owned by this module (mirrors of revisions projects_0001 and
projects_0002).

`sort_key` compares bytewise (`COLLATE "C"`), so Postgres orders the fractional keys as
Python and TypeScript do (P0-17)."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from tumnis.core.base import Base, TenantBase


class Project(TenantBase, Base):
    __tablename__ = "projects"

    name: Mapped[str]
    client: Mapped[str | None]
    goal: Mapped[str | None]
    deadline: Mapped[date | None]
    status: Mapped[str] = mapped_column(server_default=text("'active'"))
    sort_key: Mapped[str] = mapped_column(Text(collation="C"))
    code_path: Mapped[str | None]
    repo_url: Mapped[str | None]
    profile_name: Mapped[str | None]
    archived_at: Mapped[datetime | None]
    local_decisions_only: Mapped[bool] = mapped_column(server_default=text("false"))
    focus_cadence_min: Mapped[int | None]
    subtask_threshold_min: Mapped[int | None]


class ProjectLink(TenantBase, Base):
    __tablename__ = "project_links"

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str]
    value: Mapped[str]


class ProjectPolicy(TenantBase, Base):
    __tablename__ = "project_policies"

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    gated: Mapped[list[str]] = mapped_column(ARRAY(Text))
    allowed: Mapped[list[str]] = mapped_column(ARRAY(Text))
    tool_allowlist: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    max_concurrent_runs: Mapped[int]
    max_run_minutes: Mapped[int]
    max_tasks_per_run: Mapped[int]


class ProjectArchive(TenantBase, Base):
    """Where a project's archive stands (projects_0002, P2-18); no row while it is live."""

    __tablename__ = "project_archives"

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    state: Mapped[str]
