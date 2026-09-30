"""coolify public functions and DTOs; the only file other modules may import (P2-14,
FR-12.2).

A project links Coolify applications by UUID through its links (kind `coolify_app`, owned
by projects, P0-17). The worker polls each linked application every five minutes
(`workflows.poll_tick`) with the workspace's read-only token and records here, per
application, the last deployment (status, time, commit) and the preview links of open
pull requests; a failed poll keeps the last good status and marks it out of date. Reads
(`deploy_status`) come from that table only: the api process never calls Coolify.

The Coolify base URL and token are the settings section `coolify` (Settings, sealed with
the workspace data key; the token is write-only over HTTP). Create the token with read
permission only: the API cannot tell Tumnis what a token may do, so the read-only
guarantee is the adapter's (T-P2-14-02, 03).

Open pull requests come from the github module's artifacts once it exists (P2-13); until
then, and without GitHub configured, every preview in the recent deployments is listed.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, BaseModel
from sqlalchemy import Table, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.live import mark_changed
from tumnis.core.settings_store import SettingSection, get_setting, register_section
from tumnis.core.tenancy import WorkspaceContext
from tumnis.modules.coolify.models import DeploymentStatus
from tumnis.modules.coolify.rules import (
    ApplicationView,
    DeploymentView,
    latest_deployment,
    preview_prs,
    preview_urls,
)
from tumnis.modules.projects import api as projects

_status: Table = DeploymentStatus.__table__  # type: ignore[assignment]

SETTINGS_SECTION: Final = "coolify"
PollError = Literal["unavailable", "rejected"]


def _base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if value and not value.startswith(("https://", "http://")):
        raise ValueError("base_url must start with https:// or http://")
    return value


class CoolifySettings(BaseModel):
    """Settings > Coolify: where the API is and a read-only token (secret)."""

    base_url: Annotated[str, AfterValidator(_base_url)] = ""  # e.g. https://coolify.lan
    token: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)


register_section(
    SettingSection(SETTINGS_SECTION, CoolifySettings, secret_fields=frozenset({"token"}))
)


class LastDeployOut(BaseModel):
    status: str  # queued, in_progress, finished, failed, cancelled-by-user
    commit: str | None
    created_at: AwareDatetime  # when Coolify started it
    finished_at: AwareDatetime | None


class PreviewOut(BaseModel):
    pull_request_id: int
    url: str
    status: str
    commit: str | None = None
    finished_at: AwareDatetime | None = None


class AppDeployStatus(BaseModel):
    app_uuid: str
    name: str | None  # None until the first successful poll
    last: LastDeployOut | None  # None: no deployment yet (or not polled yet)
    previews: list[PreviewOut]
    checked_at: AwareDatetime | None  # the last successful poll
    error: PollError | None  # the last poll failed: the rest is out of date


class ProjectDeployStatus(BaseModel):
    project_id: UUID
    apps: list[AppDeployStatus]


async def get_settings(ctx: WorkspaceContext) -> CoolifySettings | None:
    stored = await get_setting(ctx, SETTINGS_SECTION, CoolifySettings)
    return stored.value if stored is not None else None


async def linked_apps(s: AsyncSession) -> list[str]:
    """Every application UUID a live project links, once each, in board order."""
    links = await projects.links_of_kind(s, "coolify_app")
    return list(dict.fromkeys(link.value for link in links))


async def _projects_of(s: AsyncSession, app_uuid: str) -> list[UUID]:
    links = await projects.links_of_kind(s, "coolify_app")
    return [link.project_id for link in links if link.value == app_uuid]


async def record_poll(
    s: AsyncSession,
    app: ApplicationView,
    deployments: Sequence[DeploymentView],
    *,
    now: datetime,
    open_prs: set[int] | None = None,
) -> AppDeployStatus:
    """Stores one successful poll of `app`: its last deployment and the previews of
    `open_prs` (None: every preview in `deployments`). Linked projects' cards refresh."""
    latest = latest_deployment(deployments)
    prs = preview_prs(deployments) if open_prs is None else open_prs
    previews = [link.model_dump(mode="json") for link in preview_urls(app, deployments, prs)]
    values = {
        "app_name": app.name,
        "status": latest.status if latest else None,
        "deployment_uuid": latest.deployment_uuid if latest else None,
        "commit": latest.commit if latest else None,
        "started_at": latest.created_at if latest else None,
        "finished_at": latest.finished_at if latest else None,
        "previews": previews,
        "checked_at": now,
        "error": None,
    }
    before = await _row(s, app.uuid)
    await s.execute(
        pg_insert(_status)
        .values(app_uuid=app.uuid, **values)
        .on_conflict_do_update(
            index_elements=[_status.c.workspace_id, _status.c.app_uuid], set_=values
        )
    )
    after = _Row.model_validate(values)
    if before is None or _shown(before) != _shown(after):  # checked_at alone is no news
        await _changed(s, app.uuid)
    return _out(app.uuid, values)


async def record_failure(s: AsyncSession, app_uuid: str, error: PollError) -> None:
    """A failed poll: the last good status stays, marked out of date."""
    written = await s.execute(
        pg_insert(_status)
        .values(app_uuid=app_uuid, error=error)
        .on_conflict_do_update(
            index_elements=[_status.c.workspace_id, _status.c.app_uuid],
            set_={"error": error},
            where=_status.c.error.is_distinct_from(error),
        )
    )
    if written.rowcount:  # type: ignore[attr-defined]
        await _changed(s, app_uuid)


async def _changed(s: AsyncSession, app_uuid: str) -> None:
    for project_id in await _projects_of(s, app_uuid):
        mark_changed(s, projects.LIVE_ENTITY, project_id)


class _Row(BaseModel):
    """A deployment_status row (or the values just written); an absent row is empty."""

    app_name: str | None = None
    status: str | None = None
    commit: str | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    previews: list[PreviewOut] = []
    checked_at: AwareDatetime | None = None
    error: PollError | None = None


def _shown(row: "_Row") -> dict[str, Any]:
    return row.model_dump(exclude={"checked_at"})


async def _row(s: AsyncSession, app_uuid: str) -> "_Row | None":
    found = (
        (await s.execute(select(_status).where(_status.c.app_uuid == app_uuid))).mappings().first()
    )
    return _Row.model_validate(dict(found)) if found is not None else None


def _out(app_uuid: str, values: Mapping[str, Any]) -> AppDeployStatus:
    row = _Row.model_validate(dict(values))
    last = None
    if row.status is not None and row.started_at is not None:
        last = LastDeployOut(
            status=row.status,
            commit=row.commit,
            created_at=row.started_at,
            finished_at=row.finished_at,
        )
    return AppDeployStatus(
        app_uuid=app_uuid,
        name=row.app_name,
        last=last,
        previews=row.previews,
        checked_at=row.checked_at,
        error=row.error,
    )


async def deploy_status(
    s: AsyncSession, *, project_ids: frozenset[UUID] | None = None
) -> list[ProjectDeployStatus]:
    """Each project that links a Coolify application, in board order, with each linked
    application's last polled status (an application not polled yet has no name and no
    deployment). `project_ids` limits them (a project-limited key, R-28)."""
    links = await projects.links_of_kind(s, "coolify_app", project_ids=project_ids)
    uuids = {link.value for link in links}
    rows = (
        (
            await s.execute(
                select(_status).where(_status.c.app_uuid.in_(uuids), _status.c.deleted_at.is_(None))
            )
        )
        .mappings()
        .all()
        if uuids
        else []
    )
    by_uuid: dict[str, Mapping[str, Any]] = {row["app_uuid"]: dict(row) for row in rows}
    out: dict[UUID, ProjectDeployStatus] = {}
    for link in links:
        entry = out.setdefault(
            link.project_id, ProjectDeployStatus(project_id=link.project_id, apps=[])
        )
        entry.apps.append(_out(link.value, by_uuid.get(link.value, {})))
    return list(out.values())


__all__ = [
    "SETTINGS_SECTION",
    "AppDeployStatus",
    "CoolifySettings",
    "LastDeployOut",
    "PollError",
    "PreviewOut",
    "ProjectDeployStatus",
    "deploy_status",
    "get_settings",
    "linked_apps",
    "record_failure",
    "record_poll",
]
