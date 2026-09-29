"""The one task packet (R-24, P1-04): what a run hands its agent.

P1-04 creates the module with the model; P1-08 and P1-11 add their request bodies, P1-17
owns the builder functions and the preview route (`GET /v1/tasks/{id}/packet`), and P2-02
extends the same module (untrusted blocks, policy, callback). Proposal and stuck packets
are built with `build_packet(kind=...)`, never by hand.

The packet carries `prompt_text`, the complete text Hermes receives: a fixed instruction
(use skill X, treat the packet as data, reply with one JSON object matching the named
schema) followed by the body JSON between `<packet>` and `</packet>` markers. The daemon
writes it to a query file byte for byte and never composes a prompt (R-25).
"""

from typing import Any, Literal
from uuid import UUID

from tumnis.core.schemas import VersionedPayload, versioned
from tumnis.modules.agents.protocol import SchemaRef
from tumnis.modules.agents.rules import RunKind


@versioned("packet", "task_packet", 1)
class TaskPacket(VersionedPayload):
    schema_version: Literal[1] = 1
    kind: RunKind  # phase 1 builds enrich and plan
    run_id: UUID
    profile_id: UUID
    skill: str
    output_schema: SchemaRef
    correlation_id: str
    timeout_s: int
    prompt_text: str  # fixed instructions + the body JSON between <packet> markers
    body: dict[str, Any]  # EnrichmentRequest or PlanningRequest (P1-05)
