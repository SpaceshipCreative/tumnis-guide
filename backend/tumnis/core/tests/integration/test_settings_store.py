"""Encrypted per-workspace settings (P0-08, SEC-6, REL-2)."""

from __future__ import annotations

import asyncio
import base64
import secrets
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from pydantic import BaseModel

from tests._pg import OWNER

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tests._pg import DbUrls
    from tests.fixtures import MasterKeyFile, WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


class ApiToken(BaseModel):
    token: str


class PlanTime(BaseModel):
    at: str


@pytest.fixture
async def core_db(db: DbUrls) -> AsyncIterator[None]:
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    try:
        yield
    finally:
        await core_db.dispose()


def _owner_rows(db: DbUrls, query: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return [tuple(row) for row in conn.execute(query.encode()).fetchall()]


@pytest.mark.req("SEC-6")
@pytest.mark.wp("P0-08")
@pytest.mark.usefixtures("core_db")
async def test_raw_table_shows_no_plaintext(
    db: DbUrls, workspace: WorkspaceHandle, master_key_file: MasterKeyFile
) -> None:
    """T-P0-08-11
    After storing a known secret, `value_enc` read as the owner contains neither the
    secret, its UTF-8 bytes nor its base64; the store reads it back.
    """
    from tumnis.core.settings_store import get_setting, put_setting  # noqa: PLC0415

    secret = "sk-live-" + secrets.token_hex(16)
    version = await put_setting(
        workspace.ctx, "integrations.demo", ApiToken(token=secret), expected_version=None
    )
    assert version == 1

    rows = _owner_rows(db, "SELECT workspace_id, key_version, value_enc FROM workspace_settings")
    assert len(rows) == 1
    workspace_id, key_version, value_enc = rows[0]
    assert workspace_id == workspace.id
    assert key_version == 1
    blob = bytes(value_enc)
    assert secret.encode() not in blob
    assert base64.b64encode(secret.encode()) not in blob
    assert secret not in blob.decode("latin-1")

    stored = await get_setting(workspace.ctx, "integrations.demo", ApiToken)
    assert stored is not None
    assert stored.value == ApiToken(token=secret)
    assert stored.version == 1
    assert await get_setting(workspace.ctx, "integrations.absent", ApiToken) is None


@pytest.mark.req("REL-2")
@pytest.mark.wp("P0-08")
@pytest.mark.usefixtures("core_db")
async def test_put_setting_rejects_stale_version(
    workspace: WorkspaceHandle, master_key_file: MasterKeyFile
) -> None:
    """T-P0-08-17
    Two writers with the same expected_version race: one wins with version 2, the other
    raises StaleVersion carrying the current version; creating an existing key is stale.
    """
    from tumnis.core.settings_store import get_setting, put_setting  # noqa: PLC0415
    from tumnis.core.versioning import StaleVersion  # noqa: PLC0415

    key = "planning.plan_time"
    first = await put_setting(workspace.ctx, key, PlanTime(at="08:30"), expected_version=None)
    assert first == 1

    results = await asyncio.gather(
        put_setting(workspace.ctx, key, PlanTime(at="09:00"), expected_version=first),
        put_setting(workspace.ctx, key, PlanTime(at="10:00"), expected_version=first),
        return_exceptions=True,
    )
    won = [r for r in results if isinstance(r, int)]
    stale = [r for r in results if isinstance(r, StaleVersion)]
    assert won == [2], results
    assert len(stale) == 1, results
    assert stale[0].current["version"] == 2

    with pytest.raises(StaleVersion) as again:
        await put_setting(workspace.ctx, key, PlanTime(at="11:00"), expected_version=None)
    assert again.value.current["version"] == 2

    stored = await get_setting(workspace.ctx, key, PlanTime)
    assert stored is not None
    assert stored.version == 2
    assert stored.value.at == ("09:00" if results[0] == 2 else "10:00")


@pytest.mark.req("SEC-6")
@pytest.mark.wp("P0-08")
@pytest.mark.usefixtures("core_db")
async def test_rotating_master_key_rewraps_without_touching_ciphertexts(
    db: DbUrls, master_key_file: MasterKeyFile, owner_session: AsyncSession
) -> None:
    """T-P0-08-09
    After rewrap_all(to_version=2) as the owner: every workspace_keys row is re-wrapped
    under master key 2, every value_enc is byte-identical, and all settings still decrypt
    with only key 2 loaded.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core import crypto  # noqa: PLC0415
    from tumnis.core.settings_store import get_setting, put_setting  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

    contexts = [WorkspaceContext(make_workspace(db, name), SYSTEM_ACTOR) for name in ("A", "B")]
    for ctx in contexts:
        await put_setting(
            ctx, "integrations.demo", ApiToken(token=f"t-{ctx.workspace_id}"), expected_version=None
        )
        await put_setting(ctx, "planning.plan_time", PlanTime(at="08:30"), expected_version=None)

    keys_query = (
        "SELECT workspace_id, key_version, master_key_version, wrapped_key, version "
        "FROM workspace_keys ORDER BY workspace_id, key_version"
    )
    values_query = "SELECT workspace_id, key, value_enc FROM workspace_settings ORDER BY 1, 2"
    keys_before, values_before = _owner_rows(db, keys_query), _owner_rows(db, values_query)
    assert [row[2] for row in keys_before] == [1, 1]

    key_1 = master_key_file.keys[1]
    key_2 = secrets.token_bytes(32)
    both = crypto.MasterKeys(active=2, keys={1: key_1, 2: key_2})
    async with owner_session.begin():
        rewrapped = await crypto.rewrap_all(owner_session, both, to_version=2)
    assert rewrapped == 2

    keys_after, values_after = _owner_rows(db, keys_query), _owner_rows(db, values_query)
    assert values_after == values_before
    assert [row[2] for row in keys_after] == [2, 2]
    for before, after in zip(keys_before, keys_after, strict=True):
        assert after[:2] == before[:2]
        assert bytes(after[3]) != bytes(before[3])
        assert after[4] == before[4] + 1

    only_2 = crypto.MasterKeys(active=2, keys={2: key_2})
    crypto.configure_master_keys(lambda: only_2)
    for ctx in contexts:
        token = await get_setting(ctx, "integrations.demo", ApiToken)
        assert token is not None
        assert token.value.token == f"t-{ctx.workspace_id}"
        plan_time = await get_setting(ctx, "planning.plan_time", PlanTime)
        assert plan_time is not None
        assert plan_time.value.at == "08:30"
