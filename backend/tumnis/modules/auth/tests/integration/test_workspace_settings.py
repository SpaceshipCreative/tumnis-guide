"""The workspace settings resource: timezone and subtask threshold, one version (P0-08,
R-14, REL-6, FR-3.8)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import pytest

from tests._pg import APP
from tumnis.core.tests.integration._probe import cache_probe

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import MasterKeyFile, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.fixture
async def core_db(db: DbUrls) -> AsyncIterator[None]:
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    try:
        yield
    finally:
        await core_db.dispose()


@pytest.mark.req("REL-6", "FR-3.8")
@pytest.mark.wp("P0-08")
@pytest.mark.usefixtures("core_db")
async def test_get_returns_timezone_threshold_and_version(workspace: WorkspaceHandle) -> None:
    """T-P0-08-18
    A new workspace reads its timezone, subtask_threshold_min = 30 and its version through
    get_workspace_settings.
    """
    from tumnis.modules.auth.api import get_workspace_settings  # noqa: PLC0415

    settings = await get_workspace_settings(workspace.ctx)
    assert settings.timezone == "America/New_York"
    assert settings.subtask_threshold_min == 30
    assert settings.version == 1


@pytest.mark.req("REL-6", "REL-2")
@pytest.mark.wp("P0-08")
@pytest.mark.usefixtures("core_db")
async def test_put_updates_bumps_version_and_rejects_stale_or_invalid(
    db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P0-08-19
    A put of {timezone: Australia/Sydney, version: v} returns the new zone with version
    v + 1; a second put with v raises StaleVersion carrying the current version; EST gives
    `invalid_timezone`; a threshold of 4 or 481 gives `validation_error`; a committed change
    drops the cached resource in another process, and the next get reflects it.
    """
    from tumnis.core.versioning import StaleVersion  # noqa: PLC0415
    from tumnis.modules.auth.api import (  # noqa: PLC0415
        WorkspaceSettingsIn,
        WorkspaceSettingsInvalid,
        get_workspace_settings,
        put_workspace_settings,
    )

    ctx, now = workspace.ctx, clock.now()
    v = (await get_workspace_settings(ctx)).version

    updated = await put_workspace_settings(
        ctx, WorkspaceSettingsIn(timezone="Australia/Sydney", version=v), now=now
    )
    assert updated.timezone == "Australia/Sydney"
    assert updated.subtask_threshold_min == 30
    assert updated.version == v + 1
    assert await get_workspace_settings(ctx) == updated

    with pytest.raises(StaleVersion) as stale:
        await put_workspace_settings(ctx, WorkspaceSettingsIn(timezone="UTC", version=v), now=now)
    assert stale.value.current["version"] == v + 1

    with pytest.raises(WorkspaceSettingsInvalid) as bad_zone:
        await put_workspace_settings(
            ctx, WorkspaceSettingsIn(timezone="EST", version=v + 1), now=now
        )
    assert bad_zone.value.code == "invalid_timezone"
    for threshold in (4, 481):
        with pytest.raises(WorkspaceSettingsInvalid) as bad_threshold:
            await put_workspace_settings(
                ctx, WorkspaceSettingsIn(subtask_threshold_min=threshold, version=v + 1), now=now
            )
        assert bad_threshold.value.code == "validation_error"

    longer = await put_workspace_settings(
        ctx, WorkspaceSettingsIn(subtask_threshold_min=45, version=v + 1), now=now
    )
    assert (longer.timezone, longer.subtask_threshold_min) == ("Australia/Sydney", 45)
    assert longer.version == v + 2

    key = f"ws:{workspace.id}:settings:workspace"
    async with cache_probe(db.libpq(APP), key) as probe:
        await put_workspace_settings(
            ctx, WorkspaceSettingsIn(timezone="Europe/London", version=v + 2), now=now
        )
        line = await probe.line(wait_s=5.0)
    assert line is not None
    assert line.startswith("gone"), line
    current = await get_workspace_settings(ctx)
    assert (current.timezone, current.version) == ("Europe/London", v + 3)


@pytest.mark.req("REL-6", "REL-2")
@pytest.mark.wp("P0-08")
async def test_settings_routes_over_http(
    request: pytest.FixtureRequest, master_key_file: MasterKeyFile
) -> None:
    """T-P0-08-20
    Through session_client: GET /v1/settings/workspace is 200; a PUT with Idempotency-Key
    and CSRF is 200 with version + 1; a stale version is 409 `stale_version` with `current`;
    a bad zone is 422 `invalid_timezone`.
    """
    client = request.getfixturevalue("session_client")  # P0-13

    got = await client.get("/v1/settings/workspace")
    assert got.status_code == 200, got.text
    body = got.json()
    assert set(body) == {"timezone", "subtask_threshold_min", "version"}
    v = body["version"]

    def put(payload: dict[str, object]) -> Any:
        return client.put(
            "/v1/settings/workspace",
            json=payload,
            headers={"Idempotency-Key": f"settings-{uuid.uuid4()}"},
        )

    ok = await put({"timezone": "Australia/Sydney", "version": v})
    assert ok.status_code == 200, ok.text
    assert ok.json()["timezone"] == "Australia/Sydney"
    assert ok.json()["version"] == v + 1

    stale = await put({"timezone": "UTC", "version": v})
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "stale_version"
    assert stale.json()["current"]["version"] == v + 1

    bad = await put({"timezone": "EST", "version": v + 1})
    assert bad.status_code == 422, bad.text
    assert bad.json()["code"] == "invalid_timezone"
