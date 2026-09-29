"""The daemon's protocol copies against the committed JSON Schemas (P1-04, FR-5.11, R-25).

Every message the daemon builds, and every server message it parses (the server's golden
examples in `backend/tests/contract/fixtures/runner/`), validates against
`schemas/runner/v1/<type>.json`, so the daemon's models and the server's cannot drift. The
daemon reads those files; it never imports the backend.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from jsonschema import Draft202012Validator

from tests.conftest import RECORDINGS, REPO, make_run
from tumnis_daemon.protocol import (
    HealthReport,
    ProvisionResult,
    envelope,
    make_ack,
    make_heartbeat,
    make_register,
    parse_server,
)
from tumnis_daemon.runner import build_result, read_recording

if TYPE_CHECKING:
    from pydantic import BaseModel

pytestmark = [pytest.mark.contract]

SERVER_TYPES = ("registered", "run", "health_check", "provision", "ack", "error")


def _validator(type_: str) -> Draft202012Validator:
    schema = json.loads((REPO / "schemas/runner/v1" / f"{type_}.json").read_text())
    return Draft202012Validator(schema)


def _built() -> list[BaseModel]:
    run = make_run()
    events, final = read_recording(RECORDINGS / "enrich_ok.jsonl")
    request_id = uuid.uuid4()
    return [
        make_register(
            runner_name="homelab-hermes",
            host="hermes.example.org",
            os="linux",
            daemon_version="0.1.0",
            hermes_version="0.9.0",
            profiles=["tumnis-master", "acme-site"],
            running_run_ids=[run.run_id],
        ),
        make_heartbeat("homelab-hermes", 7, [run.run_id]),
        make_ack(run),
        build_result(run, events, final, exit_code=0, timed_out=False, duration_ms=1234),
        build_result(run, [], None, exit_code=None, timed_out=True, duration_ms=600_000),
        HealthReport(
            **envelope(f"req:{request_id}"),
            request_id=request_id,
            profile="acme-site",
            profile_exists=True,
            reachable=True,
            authenticated=None,
            hermes_version=None,
            mcp_servers=["tumnis"],
        ),
        ProvisionResult(
            **envelope(f"req:{request_id}"),
            request_id=request_id,
            profile="acme-site",
            status="failed",
            distribution_version=None,
            error_code="hermes_error",
            error="not in P1-04",
        ),
    ]


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P1-04")
@pytest.mark.xfail(strict=True, reason="spec:P1-04")
def test_daemon_messages_match_committed_schemas() -> None:
    """T-P1-04-03
    Every message the daemon builds (register, heartbeat, ack, result, health_report,
    provision_result) and every server message it parses validates against the committed
    JSON Schema of its type.
    """
    built = _built()
    assert {m.type for m in built} == {  # type: ignore[attr-defined]
        "register",
        "heartbeat",
        "ack",
        "result",
        "health_report",
        "provision_result",
    }
    for message in built:
        data: dict[str, Any] = json.loads(message.model_dump_json())
        _validator(data["type"]).validate(data)

    for type_ in SERVER_TYPES:
        path = REPO / "backend/tests/contract/fixtures/runner" / type_ / "v1.json"
        example = json.loads(path.read_text())
        _validator(type_).validate(example)
        parsed = parse_server(json.dumps(example))
        assert parsed.type == type_
        assert json.loads(parsed.model_dump_json()) == example
