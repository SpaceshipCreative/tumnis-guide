"""agents DBOS workflows and steps (P1-04): `run_skill`, `runner_sweep`,
`check_profile_health`."""

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from tumnis.modules.agents.packet_builder import TaskPacket

if TYPE_CHECKING:
    from dbos import DBOSClient, WorkflowHandleAsync


async def start_run_skill(
    workspace_id: UUID, packet: TaskPacket
) -> "WorkflowHandleAsync[dict[str, Any]]":
    raise NotImplementedError(f"P1-04 {workspace_id} {packet}")


async def enqueue_run_skill(client: "DBOSClient", workspace_id: UUID, packet: TaskPacket) -> str:
    raise NotImplementedError(f"P1-04 {client} {workspace_id} {packet}")


async def runner_sweep(scheduled_at: datetime, context: Any) -> list[str]:
    raise NotImplementedError(f"P1-04 {scheduled_at} {context}")
