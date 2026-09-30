"""The daemon's archive messages against the committed JSON Schemas (P2-18, FR-5.10).

`archive_done` and `restore_done` as the daemon builds them validate against
`schemas/runner/v1/<type>.json`, and the server's golden `restore` and `purge_archive`
examples parse here and dump back unchanged, so the daemon's copies and the server's
cannot drift.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from jsonschema import Draft202012Validator

from tests.conftest import REPO
from tumnis_daemon.protocol import CAPABILITIES, ArchiveDone, RestoreDone, envelope, parse_server

if TYPE_CHECKING:
    from pydantic import BaseModel

pytestmark = [pytest.mark.contract]

FIXTURES = REPO / "backend/tests/contract/fixtures/runner"


def _validator(type_: str) -> Draft202012Validator:
    schema = json.loads((REPO / "schemas/runner/v1" / f"{type_}.json").read_text())
    return Draft202012Validator(schema)


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P2-18")
def test_archive_messages_match_committed_schemas() -> None:
    archive_id = "01950000-0000-7000-8000-000000000501"
    built: list[BaseModel] = [
        ArchiveDone(
            **envelope("req:1"),
            archive_id=archive_id,
            path=f"/var/lib/tumnis-daemon/archives/{archive_id}.tar.zst",
            size=1234,
            sha256="a" * 64,
            manifest_digest="b" * 64,
        ),
        ArchiveDone(
            **envelope("req:1"),
            archive_id=archive_id,
            path="",
            size=0,
            sha256="",
            manifest_digest="",
            error_code="active_run",
            error="a run of this profile is in progress",
        ),
        RestoreDone(**envelope("req:2"), archive_id=archive_id, manifest_digest="b" * 64, ok=True),
        RestoreDone(
            **envelope("req:2"), archive_id=archive_id, manifest_digest="", ok=False, error="no"
        ),
    ]
    for message in built:
        data: dict[str, Any] = json.loads(message.model_dump_json())
        _validator(data["type"]).validate(data)

    for type_ in ("archive", "restore", "purge_archive"):
        example = json.loads((FIXTURES / type_ / "v1.json").read_text())
        _validator(type_).validate(example)
        parsed = parse_server(json.dumps(example))
        assert parsed.type == type_
        assert json.loads(parsed.model_dump_json()) == example

    for type_ in ("archive_done", "restore_done"):
        example = json.loads((FIXTURES / type_ / "v1.json").read_text())
        _validator(type_).validate(example)
    assert "archive" in CAPABILITIES
