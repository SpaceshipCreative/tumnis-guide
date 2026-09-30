"""planning event payload models and subscribers.

- `calendar.synced` -> `planning.drop_day_calendars`: a finished sync may have changed any
  day's events, so every cached day calendar of the workspace is dropped (P1-10). The
  drop is idempotent: running it again drops nothing new.
"""

from tumnis.core.cache import invalidate_on_commit
from tumnis.core.events import EventEnvelope, subscribe
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.planning import api


@subscribe("calendar.synced", name="planning.drop_day_calendars")
async def drop_day_calendars(envelope: EventEnvelope) -> None:
    async with tenant_session(WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)) as s:
        await invalidate_on_commit(s, tag=api.free_blocks_tag(envelope.workspace_id))
