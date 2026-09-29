"""tasks event payload models and subscribers (P0-18). The payload models live in
`payloads.py` (re-exported here) so `api.py` can emit them without importing this module.

Subscriber `tasks.create_default_columns` (never rename it: the name is part of every
delivery's workflow ID): on `project.created`, the project's six default board columns.
Idempotent: the api writes them only while the project has none.
"""

from uuid import UUID

from tumnis.core.events import EventEnvelope, subscribe
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
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
]


@subscribe("project.created", name="tasks.create_default_columns")
async def create_default_columns(envelope: EventEnvelope) -> None:
    project_id = UUID(str(envelope.payload["project_id"]))
    async with tenant_session(WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)) as s:
        await api.ensure_default_columns(s, project_id)
