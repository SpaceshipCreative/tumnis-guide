"""The Coolify status port (P2-14, FR-12.2): the whole surface Tumnis has on Coolify is
these three reads. There is no write method, and the real adapter's HTTP wrapper refuses
anything but GET and HEAD (`coolify_status.ReadOnlyHttp`): deployments are the agents' to
trigger, through the Coolify MCP server under the approval policy.

The token Tumnis stores should be created with read permission only; Coolify's API cannot
tell Tumnis what a token may do, so the read-only guarantee is this adapter's.
"""

from typing import Protocol

from tumnis.core.adapters.registry import Health
from tumnis.modules.coolify.rules import ApplicationView, DeploymentView

__all__ = ["ApplicationView", "CoolifyStatus", "DeploymentView", "Health"]

TAKE_DEFAULT = 10  # Coolify's own default page size for deployments


class CoolifyStatus(Protocol):
    """The whole public surface; no write methods (T-P2-14-02)."""

    async def get_application(self, app_uuid: str) -> ApplicationView: ...

    async def list_deployments(
        self, app_uuid: str, take: int = TAKE_DEFAULT
    ) -> list[DeploymentView]: ...

    async def health(self) -> Health: ...
