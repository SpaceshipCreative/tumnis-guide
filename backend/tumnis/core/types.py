"""Shared pure types. rules.py may import this module, so it does no I/O and imports
only what the rules allow-list permits (T-P0-01-09)."""

import re
from typing import Final, Literal, NewType
from uuid import UUID

WorkspaceId = NewType("WorkspaceId", UUID)

# "system" or "<kind>:<uuid>"; the same shape P0-06's ACTOR_CHECK enforces on created_by.
ActorRef = NewType("ActorRef", str)

SYSTEM_ACTOR: Final = ActorRef("system")
ACTOR_REF_PATTERN: Final = re.compile(r"^(system|(user|api_key|task_token|device):[0-9a-f-]{36})$")

# Where this deployment runs (A11 DEPLOYMENT_MODE): the SSRF guard allows the private LAN
# only when self-hosted (P0-16).
DeploymentMode = Literal["self-hosted", "hosted"]
