"""integrations DBOS workflows and steps.

P2-18: importing `archive` registers the excerpt steps of the project archive workflows.
"""

from datetime import datetime
from typing import Any

from tumnis.modules.integrations import archive as _archive  # noqa: F401

# --- P3-02 spec stubs --------------------------------------------------------------------------


def use(**_: Any) -> dict[str, Any]:
    raise NotImplementedError


async def connector_sync(workspace_id: str, connection_id: str) -> dict[str, Any]:
    raise NotImplementedError


async def connector_sync_tick(scheduled_at: datetime, context: Any) -> None:
    raise NotImplementedError
