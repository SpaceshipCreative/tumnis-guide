"""integrations event payload models and subscribers. The payload models live in
`payloads.py` (re-exported here) so `api.py` can emit them.

Subscriber (P3-09): `integrations.start_purge` starts `purge_scope` (workflow
`purge:<id>`, on the maintenance queue) on the `purge.requested` of a user's purge; the
retention purge runs its own as a child workflow. The workflows module is imported when
the subscriber runs: api imports this module's payloads, and workflows imports api.
"""

import importlib
from typing import Any

from tumnis.core.events import EventEnvelope, subscribe
from tumnis.modules.integrations.payloads import (
    ArtifactUpdatedV1,
    ConnectionAuthRequiredV1,
    ItemsIngestedV1,
    ItemsPurgedV1,
    PurgeRequestedV1,
)

__all__ = [
    "ArtifactUpdatedV1",
    "ConnectionAuthRequiredV1",
    "ItemsIngestedV1",
    "ItemsPurgedV1",
    "PurgeRequestedV1",
]


def _workflows() -> Any:
    return importlib.import_module("tumnis.modules.integrations.workflows")


@subscribe("purge.requested", name="integrations.start_purge")
async def start_purge(envelope: EventEnvelope) -> None:
    if envelope.payload.get("scope") == "retention":
        return  # the retention workflow runs its purge itself
    await _workflows().start_purge(str(envelope.workspace_id), str(envelope.payload["purge_id"]))
