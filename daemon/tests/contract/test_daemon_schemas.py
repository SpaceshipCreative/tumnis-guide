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


# P2-07: protocol 2 (daemon -> server built here; server -> daemon parsed here).
V2_SERVER_EXAMPLES = (("run", 2), ("ack", 2), ("cancel", 1), ("archive", 1), ("nack", 1))


def _validator_v(type_: str, version: int) -> Draft202012Validator:
    schema = json.loads((REPO / f"schemas/runner/v{version}" / f"{type_}.json").read_text())
    return Draft202012Validator(schema)


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P2-07")
def test_protocol2_messages_match_committed_schemas() -> None:
    """The protocol-2 messages the daemon builds (stream, status, upload_artifact, the
    batched ack, the version-2 result, a heartbeat with its outbox depth) and the server's
    protocol-2 examples it parses validate against the committed schema of their type and
    version."""
    from tumnis_daemon.protocol import (  # noqa: PLC0415
        make_ack_batch,
        make_artifact,
        make_status,
        make_stream,
    )
    from tumnis_daemon.runner import as_v2  # noqa: PLC0415

    run = make_run()
    corr = run.correlation_id
    events, final = read_recording(RECORDINGS / "enrich_ok.jsonl")
    result = build_result(run, events, final, exit_code=0, timed_out=False, duration_ms=5)
    built: list[BaseModel] = [
        make_stream(run.run_id, corr, 1, "log", "Reading the packet." * 1000),
        make_stream(run.run_id, corr, 2, "tool_call", '{"name": "skill_view"}'),
        make_stream(run.run_id, corr, 3, "file_touched", "src/app.py"),
        make_status(run.run_id, corr, "started", profile="acme-site", profile_version="1.4.0"),
        make_status(run.run_id, corr, "cancelling", detail="stopped by the user"),
        make_artifact(run.run_id, corr, "notes.md", "text/markdown", "# Notes\n"),
        make_ack_batch(corr, [run.message_id]),
        as_v2(result),
        as_v2(result, status="cancelled", output_json=None, error="cancelled"),
        make_heartbeat("homelab-hermes", 3, [run.run_id], outbox_depth=12),
    ]
    for message in built:
        data: dict[str, Any] = json.loads(message.model_dump_json())
        _validator_v(data["type"], data["schema_version"]).validate(data)

    for type_, version in V2_SERVER_EXAMPLES:
        path = REPO / "backend/tests/contract/fixtures/runner" / type_ / f"v{version}.json"
        example = json.loads(path.read_text())
        _validator_v(type_, version).validate(example)
        parsed = parse_server(json.dumps(example))
        assert parsed.type == type_
        assert parsed.schema_version == version
        assert json.loads(parsed.model_dump_json()) == example
