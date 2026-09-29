"""The task packet (P1-04, R-24) holds the limits the runner protocol's `run` holds, so a
packet the daemon transport could not send fails when it is built."""

import uuid

import pytest
from pydantic import ValidationError

from tumnis.modules.agents.packet_builder import TaskPacket
from tumnis.modules.agents.protocol import SchemaRef
from tumnis.modules.agents.rules import RunKind


def _packet(**overrides: object) -> TaskPacket:
    run_id = uuid.uuid4()
    fields: dict[str, object] = {
        "kind": RunKind.ENRICH,
        "run_id": run_id,
        "profile_id": uuid.uuid4(),
        "skill": "enrich",
        "output_schema": SchemaRef(family="enrichment", name="result", version=1),
        "correlation_id": f"run:{run_id}",
        "timeout_s": 60,
        "prompt_text": "Use the skill enrich.\n<packet>\n{}\n</packet>\n",
        "body": {},
        **overrides,
    }
    return TaskPacket.model_validate(fields)


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P1-04")
@pytest.mark.parametrize(
    "overrides",
    [
        {"timeout_s": 9},
        {"timeout_s": 3601},
        {"skill": "Enrich"},
        {"skill": "enrich; rm -rf /"},
        {"correlation_id": "x" * 129},
    ],
    ids=["timeout_low", "timeout_high", "skill_case", "skill_shell", "correlation_long"],
)
def test_packet_holds_the_run_limits(overrides: dict[str, object]) -> None:
    """`skill` matches SKILL_RE, `timeout_s` is 10-3600 and `correlation_id` is at most 128
    characters, as on the protocol's `run`."""
    assert _packet().timeout_s == 60
    assert _packet(timeout_s=10, skill="plan", correlation_id="x" * 128).skill == "plan"
    with pytest.raises(ValidationError):
        _packet(**overrides)
