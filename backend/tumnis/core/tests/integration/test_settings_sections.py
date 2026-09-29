"""GET/PUT /v1/settings/{section} and the module switches under /v1/settings/modules
(P0-26, SEC-3, SEC-6): sealed, write-only secrets, versioned writes and audit rows."""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import MasterKeyFile, WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

SECTION = "test.sections_secret"
PLAINTEXT = "sk-test-5d1c9b7e0f3a4a8e9b2c"


class _SecretSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str | None = None
    label: str = ""


def _register() -> None:
    from tumnis.core.settings_store import SettingSection, register_section  # noqa: PLC0415

    register_section(SettingSection(SECTION, _SecretSection, secret_fields=frozenset({"token"})))


def _put(client: Any, path: str, payload: dict[str, Any]) -> Any:
    return client.put(path, json=payload, headers={"Idempotency-Key": f"s-{uuid.uuid4()}"})


@pytest.mark.req("SEC-6", "SEC-3")
@pytest.mark.wp("P0-26")
@pytest.mark.xfail(strict=True, reason="spec:P0-26")
async def test_secret_section_is_sealed_write_only_and_audited(
    request: pytest.FixtureRequest, db: DbUrls, master_key_file: MasterKeyFile
) -> None:
    """T-P0-26-12
    A registered section reads as `{section, values, secrets_set, version}` with its secret
    fields left out of `values`. A PUT at version null creates it (version 1), a PUT at the
    current version merges the given fields (an omitted secret keeps its value), a stale
    version is 409 `stale_version`, a field the model refuses is 422 `validation_error`,
    and an unknown section is 404 `not_found`. The plaintext is in no response, not in the
    stored row, and not in the `settings.changed` audit rows (one per write, naming the
    section and the fields).
    """
    from tumnis.core.tests.integration._audit import owner_rows  # noqa: PLC0415

    _register()
    client = request.getfixturevalue("session_client")
    path = f"/v1/settings/{SECTION}"

    empty = await client.get(path)
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"section": SECTION, "values": {}, "secrets_set": [], "version": None}

    created = await _put(
        client, path, {"values": {"token": PLAINTEXT, "label": "ci"}, "version": None}
    )
    assert created.status_code == 200, created.text
    assert created.json() == {
        "section": SECTION,
        "values": {"label": "ci"},
        "secrets_set": ["token"],
        "version": 1,
    }

    again = await client.get(path)
    assert again.json() == created.json()

    stale = await _put(client, path, {"values": {"label": "late"}, "version": None})
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "stale_version"

    relabel = await _put(client, path, {"values": {"label": "nightly"}, "version": 1})
    assert relabel.status_code == 200, relabel.text
    assert relabel.json() == {
        "section": SECTION,
        "values": {"label": "nightly"},
        "secrets_set": ["token"],
        "version": 2,
    }

    refused = await _put(client, path, {"values": {"nope": 1}, "version": 2})
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "validation_error"

    unknown = await client.get("/v1/settings/test.no_such_section")
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["code"] == "not_found"

    for response in (empty, created, again, stale, relabel, refused):
        assert PLAINTEXT not in response.text
    stored = owner_rows(db, "SELECT value_enc FROM workspace_settings WHERE key = %s", (SECTION,))
    assert len(stored) == 1
    assert PLAINTEXT.encode() not in bytes(stored[0][0])

    audited = owner_rows(
        db, "SELECT details FROM audit_log WHERE action = 'settings.changed' ORDER BY seq"
    )
    assert [row[0] for row in audited] == [
        {"section": SECTION, "fields": ["label", "token"]},
        {"section": SECTION, "fields": ["label"]},
    ]
    assert PLAINTEXT not in json.dumps([row[0] for row in audited])


@pytest.mark.req("SEC-3", "Hosted readiness")
@pytest.mark.wp("P0-26")
@pytest.mark.xfail(strict=True, reason="spec:P0-26")
async def test_module_switches_over_http(
    request: pytest.FixtureRequest,
    db: DbUrls,
    workspace: WorkspaceHandle,
    master_key_file: MasterKeyFile,
) -> None:
    """T-P0-26-13
    `GET /v1/settings/modules` lists every registered module with `enabled` and `required`;
    `PUT /v1/settings/modules/{module}` with `{enabled}` switches it for the workspace
    (`module.toggled`, one row) and answers the new state; a required module is 422
    `module_required` and an unknown one 404 `not_found`, neither audited.
    """
    from tumnis.core.modules import MODULES, REQUIRED_MODULES, enabled  # noqa: PLC0415
    from tumnis.core.tests.integration._audit import owner_rows  # noqa: PLC0415

    client = request.getfixturevalue("session_client")

    listed = await client.get("/v1/settings/modules")
    assert listed.status_code == 200, listed.text
    items = {item["module"]: item for item in listed.json()["items"]}
    assert set(items) == set(MODULES)
    assert items["calendar"] == {"module": "calendar", "enabled": True, "required": False}
    assert all(items[name]["required"] for name in REQUIRED_MODULES)

    off = await _put(client, "/v1/settings/modules/calendar", {"enabled": False})
    assert off.status_code == 200, off.text
    assert off.json() == {"module": "calendar", "enabled": False, "required": False}
    assert await enabled("calendar", workspace.id) is False
    relisted = {i["module"]: i for i in (await client.get("/v1/settings/modules")).json()["items"]}
    assert relisted["calendar"]["enabled"] is False

    required = await _put(client, "/v1/settings/modules/tasks", {"enabled": False})
    assert required.status_code == 422, required.text
    assert required.json()["code"] == "module_required"

    unknown = await _put(client, "/v1/settings/modules/nope", {"enabled": False})
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["code"] == "not_found"

    rows = owner_rows(db, "SELECT details FROM audit_log WHERE action = 'module.toggled'")
    assert rows == [({"module": "calendar", "enabled": False},)]
