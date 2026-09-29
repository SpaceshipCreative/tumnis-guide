"""Provider configs (P1-01, FR-11.1): the decisions slot's provider, fallback, pinned model
and credential, sealed with the workspace data key."""

from __future__ import annotations

import base64
import secrets
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import pytest
from pydantic import SecretStr
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.fixture
async def core_db(db: DbUrls) -> AsyncIterator[None]:
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    try:
        yield
    finally:
        await core_db.dispose()


@pytest.mark.req("FR-11.1", "SEC-6")
@pytest.mark.wp("P1-01")
@pytest.mark.usefixtures("core_db", "master_key_file")
async def test_credentials_encrypted_at_rest(
    workspace: WorkspaceHandle, owner_session: AsyncSession
) -> None:
    """T-P1-01-15
    After storing the decisions slot (primary jev, fallback vllm, model jev-1.13.0) with a
    known key, the raw `credentials_enc` read as the owner holds neither the key, its
    UTF-8 bytes nor its base64; reading through the api decrypts it; a second write bumps
    the version and replaces the key; an alias model is refused.
    """
    from tumnis.modules.decisions.api import (  # noqa: PLC0415
        ProviderConfigIn,
        get_provider_config,
        put_provider_config,
    )

    key = "ts_live_" + secrets.token_hex(16)
    config = ProviderConfigIn(
        slot="decisions",
        primary="jev",
        fallback="vllm",
        model_version="jev-1.13.0",
        api_key=SecretStr(key),
    )
    assert await put_provider_config(workspace.ctx, config) == 1

    rows = (
        await owner_session.execute(
            text(
                'SELECT workspace_id, slot, "primary", fallback, model_version, credentials_enc '
                "FROM provider_configs"
            )
        )
    ).all()
    assert len(rows) == 1
    workspace_id, slot, primary, fallback, model_version, sealed = rows[0]
    assert (workspace_id, slot, primary, fallback, model_version) == (
        workspace.id,
        "decisions",
        "jev",
        "vllm",
        "jev-1.13.0",
    )
    blob = bytes(sealed)
    assert key.encode() not in blob
    assert base64.b64encode(key.encode()) not in blob
    assert key.encode().hex().encode() not in blob

    got = await get_provider_config(workspace.ctx, "decisions")
    assert got is not None
    assert got.api_key is not None
    assert got.api_key.get_secret_value() == key
    assert (got.primary, got.fallback, got.model_version, got.version) == (
        "jev",
        "vllm",
        "jev-1.13.0",
        1,
    )
    assert key not in repr(got)

    rotated = "ts_live_" + secrets.token_hex(16)
    changed = config.model_copy(update={"api_key": SecretStr(rotated)})
    assert await put_provider_config(workspace.ctx, changed) == 2
    again = await get_provider_config(workspace.ctx, "decisions")
    assert again is not None
    assert again.api_key is not None
    assert again.api_key.get_secret_value() == rotated

    assert await get_provider_config(workspace.ctx, "generation") is None
    with pytest.raises(ValueError, match="pinned"):
        ProviderConfigIn(slot="decisions", primary="jev", model_version="jev-latest")


@pytest.mark.req("FR-11.1")
@pytest.mark.wp("P1-01")
@pytest.mark.usefixtures("core_db", "master_key_file")
async def test_put_after_soft_delete_revives_the_slot(
    workspace: WorkspaceHandle, owner_session: AsyncSession
) -> None:
    """Storing a slot whose row was soft-deleted brings the row back: the upsert clears
    `deleted_at`, so the version it returns is the config `get_provider_config` reads."""
    from tumnis.modules.decisions.api import (  # noqa: PLC0415
        ProviderConfigIn,
        get_provider_config,
        put_provider_config,
    )

    config = ProviderConfigIn(
        slot="decisions", primary="jev", fallback="vllm", model_version="jev-1.13.0"
    )
    assert await put_provider_config(workspace.ctx, config) == 1
    await owner_session.execute(
        text("UPDATE provider_configs SET deleted_at = now() WHERE slot = 'decisions'")
    )
    await owner_session.commit()
    assert await get_provider_config(workspace.ctx, "decisions") is None

    revived = config.model_copy(update={"fallback": None})
    version = await put_provider_config(workspace.ctx, revived)
    got = await get_provider_config(workspace.ctx, "decisions")
    assert got is not None
    assert (got.primary, got.fallback, got.version) == ("jev", None, version)
