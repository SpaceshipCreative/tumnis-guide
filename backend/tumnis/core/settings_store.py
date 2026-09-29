"""Per-workspace settings, sealed with the workspace data key (P0-08, SEC-6).

A setting is a pydantic model stored as JSON, sealed with AES-256-GCM under the
workspace's active data key and bound to its workspace and key by the AAD. The data key is
created on the first write and kept wrapped by the master key (tumnis.core.crypto); an
unwrapped data key lives only in this process's memory.

Reads go through the `settings` cache (no TTL), which holds the sealed value and its
version, never plaintext; put_setting invalidates `ws:<id>:settings:<key>` on commit.
"""

import re
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import crypto
from tumnis.core.cache import CacheKey, CacheSpec, invalidate_on_commit, register_cache
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.versioning import StaleVersion

FIRST_KEY_VERSION = 1
SETTINGS_CACHE = register_cache(
    CacheSpec(
        "settings",
        scope="workspace",
        ttl_s=None,
        invalidated_by=("put_setting", "put_workspace_settings (workspaces row)"),
    )
)
_ABSENT = b""  # cached "never set"


def settings_cache_key(workspace_id: UUID, key: str) -> CacheKey:
    return CacheKey.for_workspace(workspace_id, "settings", key)


@dataclass(frozen=True)
class Versioned[T]:
    value: T
    version: int


_ACTIVE_KEY = text(
    "SELECT key_version, master_key_version, wrapped_key FROM workspace_keys "
    "WHERE active AND deleted_at IS NULL ORDER BY key_version DESC LIMIT 1"
)
_KEY_BY_VERSION = text(
    "SELECT master_key_version, wrapped_key FROM workspace_keys WHERE key_version = :v"
)
_NEW_KEY = text(
    "INSERT INTO workspace_keys (key_version, master_key_version, wrapped_key, active) "
    "VALUES (:v, :mv, :wrapped, true) "
    "ON CONFLICT (workspace_id, key_version) DO NOTHING RETURNING key_version"
)
_READ = text(
    "SELECT version, value_enc FROM workspace_settings WHERE key = :key AND deleted_at IS NULL"
)
_CREATE = text(
    "INSERT INTO workspace_settings (key, value_enc, key_version) VALUES (:key, :blob, :kv) "
    "ON CONFLICT (workspace_id, key) DO NOTHING RETURNING version"
)
_UPDATE = text(
    "UPDATE workspace_settings SET value_enc = :blob, key_version = :kv "
    "WHERE key = :key AND version = :expected AND deleted_at IS NULL RETURNING version"
)
_CURRENT = text("SELECT version FROM workspace_settings WHERE key = :key AND deleted_at IS NULL")


def _unwrap(workspace_id: UUID, key_version: int, master_version: int, wrapped: bytes) -> bytes:
    data_key = crypto.unwrap(
        crypto.master_keys(),
        bytes(wrapped),
        master_version=master_version,
        workspace_id=workspace_id,
        key_version=key_version,
    )
    crypto.remember_data_key(workspace_id, key_version, data_key)
    return data_key


async def _data_key(session: AsyncSession, workspace_id: UUID, key_version: int) -> bytes:
    remembered = crypto.remembered_data_key(workspace_id, key_version)
    if remembered is not None:
        return remembered
    row = (await session.execute(_KEY_BY_VERSION, {"v": key_version})).one_or_none()
    if row is None:
        raise crypto.DecryptionError("no data key for this sealed value")
    return _unwrap(workspace_id, key_version, row.master_key_version, row.wrapped_key)


async def _sealing_key(session: AsyncSession, workspace_id: UUID) -> tuple[int, bytes]:
    """The active data key, created (and wrapped) on the workspace's first write."""
    row = (await session.execute(_ACTIVE_KEY)).one_or_none()
    if row is None:
        data_key = crypto.new_data_key()
        master_version, wrapped = crypto.wrap(
            crypto.master_keys(), data_key, workspace_id=workspace_id, key_version=FIRST_KEY_VERSION
        )
        params = {"v": FIRST_KEY_VERSION, "mv": master_version, "wrapped": wrapped}
        if (await session.execute(_NEW_KEY, params)).one_or_none() is not None:
            crypto.remember_data_key(workspace_id, FIRST_KEY_VERSION, data_key)
            return FIRST_KEY_VERSION, data_key
        row = (await session.execute(_ACTIVE_KEY)).one()  # a concurrent first write won
    key_version = int(row.key_version)
    remembered = crypto.remembered_data_key(workspace_id, key_version)
    if remembered is not None:
        return key_version, remembered
    return key_version, _unwrap(workspace_id, key_version, row.master_key_version, row.wrapped_key)


async def get_setting[M: BaseModel](
    ctx: WorkspaceContext, key: str, model: type[M]
) -> Versioned[M] | None:
    """The setting decrypted into `model` with its version, or None when it was never set."""
    cache_key = settings_cache_key(ctx.workspace_id, key)
    cached = await SETTINGS_CACHE.get(cache_key)
    if cached is None:
        token = SETTINGS_CACHE.token()
        async with tenant_session(ctx) as session:
            row = (await session.execute(_READ, {"key": key})).one_or_none()
        cached = _ABSENT if row is None else _entry(int(row.version), bytes(row.value_enc))
        await SETTINGS_CACHE.fill(cache_key, cached, since=token)
    if cached == _ABSENT:
        return None
    version, blob = int.from_bytes(cached[:4], "big"), cached[4:]
    key_version = crypto.sealed_key_version(blob)
    data_key = crypto.remembered_data_key(ctx.workspace_id, key_version)
    if data_key is None:
        async with tenant_session(ctx) as session:
            data_key = await _data_key(session, ctx.workspace_id, key_version)
    plaintext = crypto.open_sealed(
        {key_version: data_key}, blob, aad=crypto.setting_aad(ctx.workspace_id, key)
    )
    return Versioned(model.model_validate_json(plaintext), version)


def _entry(version: int, blob: bytes) -> bytes:
    return version.to_bytes(4, "big") + blob


async def put_setting(
    ctx: WorkspaceContext,
    key: str,
    value: BaseModel,
    *,
    expected_version: int | None,
    session: AsyncSession | None = None,
) -> int:
    """Seal and upsert; returns the new version. `expected_version` None means the key must
    not exist yet; a stale or unexpected version raises StaleVersion (409 in P0-10) with
    the current version. With `session` (the request's idempotent transaction, where its
    audit row goes too) it writes there; else in a transaction of its own."""
    if session is not None:
        return await _put(session, ctx, key, value, expected_version)
    async with tenant_session(ctx) as own:
        return await _put(own, ctx, key, value, expected_version)


async def _put(
    session: AsyncSession,
    ctx: WorkspaceContext,
    key: str,
    value: BaseModel,
    expected_version: int | None,
) -> int:
    key_version, data_key = await _sealing_key(session, ctx.workspace_id)
    blob = crypto.seal(
        data_key,
        key_version,
        value.model_dump_json().encode(),
        aad=crypto.setting_aad(ctx.workspace_id, key),
    )
    params = {"key": key, "blob": blob, "kv": key_version, "expected": expected_version}
    written = await session.execute(_CREATE if expected_version is None else _UPDATE, params)
    row = written.one_or_none()
    if row is None:
        current = (await session.execute(_CURRENT, {"key": key})).scalar_one_or_none()
        raise StaleVersion(current={"key": key, "version": current})
    await invalidate_on_commit(session, settings_cache_key(ctx.workspace_id, key))
    return int(row.version)


async def seal_for_workspace(
    session: AsyncSession, workspace_id: UUID, plaintext: bytes, *, aad: bytes
) -> tuple[int, bytes]:
    """Seal a value with the workspace's active data key inside the caller's transaction
    (in that workspace's context); returns (data key version, sealed blob). Other modules'
    secrets that live in their own columns use it (P0-13: users.totp_secret_enc)."""
    key_version, data_key = await _sealing_key(session, workspace_id)
    return key_version, crypto.seal(data_key, key_version, plaintext, aad=aad)


async def open_for_workspace(
    session: AsyncSession, workspace_id: UUID, blob: bytes, *, aad: bytes
) -> bytes:
    """Open a value sealed by `seal_for_workspace` (DecryptionError when it does not
    authenticate under `aad`)."""
    key_version = crypto.sealed_key_version(blob)
    data_key = await _data_key(session, workspace_id, key_version)
    return crypto.open_sealed({key_version: data_key}, blob, aad=aad)


# --- Sections (P0-26): the settings GET/PUT /v1/settings/{section} serves ------------------
#
# A module that keeps a per-workspace setting behind the Settings screen registers it as a
# section: its pydantic model and the fields that are secrets. The route answers a
# section's values with the secret fields left out (only whether each is set), so a secret
# is write-only over HTTP and never lands in an idempotent replay. `workspace` and
# `modules` have routes of their own and cannot be sections.

RESERVED_SECTIONS: Final = frozenset({"workspace", "modules"})
_SECTION_NAME: Final = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


@dataclass(frozen=True)
class SettingSection:
    name: str  # also the workspace_settings key, for example "decisions.jev"
    model: type[BaseModel]
    secret_fields: frozenset[str] = frozenset()


_sections: dict[str, SettingSection] = {}


def register_section(section: SettingSection) -> None:
    """Adds a section (the same one again is a no-op). ValueError for a reserved or
    malformed name, a name another section holds, or a secret that is not a field."""
    name = section.name
    if name in RESERVED_SECTIONS or not _SECTION_NAME.match(name):
        raise ValueError(f"{name!r} cannot be a settings section")
    unknown = sorted(section.secret_fields - set(section.model.model_fields))
    if unknown:
        raise ValueError(f"{name}: secret fields {unknown} are not fields of the model")
    held = _sections.get(name)
    if held is not None and held != section:
        raise ValueError(f"{name} is registered already with another model")
    _sections[name] = section


def registered_section(name: str) -> SettingSection | None:
    return _sections.get(name)
