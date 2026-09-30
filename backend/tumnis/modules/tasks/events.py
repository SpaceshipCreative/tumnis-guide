"""tasks event payload models and subscribers (P0-18). The payload models live in
`payloads.py` (re-exported here) so `api.py` can emit them without importing this module.

Subscriber `tasks.create_default_columns` (never rename it: the name is part of every
delivery's workflow ID): on `project.created`, the project's six default board columns.
Idempotent: the api writes them only while the project has none.

Subscriber `tasks.flag_red_checks` (P2-13): on `artifact.updated` of a pull request, the open
`result` review items that link it get the `checks_red` flag while its checks are red and
lose it when they are not. Idempotent: a flag already in the wanted state is left alone.
"""

from uuid import UUID

from tumnis.core.events import EventEnvelope, subscribe
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.github import api as github
from tumnis.modules.tasks import api
from tumnis.modules.tasks.payloads import (
    DOC_BODY_MAX_BYTES,
    HumanDecidedV1,
    TaskCreatedV1,
    TaskDoc,
    TaskStatusChangedV1,
    TaskUpdatedV1,
)

__all__ = [
    "DOC_BODY_MAX_BYTES",
    "HumanDecidedV1",
    "TaskCreatedV1",
    "TaskDoc",
    "TaskStatusChangedV1",
    "TaskUpdatedV1",
    "create_default_columns",
    "flag_red_checks",
]


@subscribe("project.created", name="tasks.create_default_columns")
async def create_default_columns(envelope: EventEnvelope) -> None:
    project_id = UUID(str(envelope.payload["project_id"]))
    async with tenant_session(WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)) as s:
        await api.ensure_default_columns(s, project_id)


@subscribe("artifact.updated", name="tasks.flag_red_checks")
async def flag_red_checks(envelope: EventEnvelope) -> None:
    payload = envelope.payload
    if payload.get("kind") != "pull_request":
        return
    key = github.pull_request_key(str(payload.get("url") or ""))
    if key is None:
        return
    checks = payload.get("checks")
    status = checks.get("pr_status") if isinstance(checks, dict) else None
    red = isinstance(status, dict) and status.get("checks") == "red"
    async with tenant_session(WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)) as s:
        await api.flag_red_checks(s, key, red=red)
