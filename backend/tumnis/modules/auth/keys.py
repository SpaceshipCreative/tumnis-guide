"""API keys, task tokens and device tokens: generate, parse, hash, verify, look up (P0-14,
SEC-2, ADR-0010).

A secret is shown once as `<kind>_<prefix>_<secret>`: `tmn_` API keys, `tmt_` task tokens,
`tmd_` device tokens. The prefix (12 base32 characters) is stored in clear for the lookup;
the secret (43 url-safe characters, 256 bits) only as HMAC-SHA256 under the server pepper
(API_KEY_PEPPER_FILE), with the pepper version it was made with. Keys are long random
secrets checked on every request, so a keyed hash, not argon2id (ADR-0010).

Lookups by prefix run through the SECURITY DEFINER functions `app.auth_resolve_api_key`
and `app.auth_resolve_token` (the workspace is unknown until then) and are cached in the
system-scope cache `auth.api_key_by_prefix`; revoking, rotating or reissuing drops the
entry in every process on commit (LISTEN/NOTIFY), so a revoked secret dies within a second.
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal, Self, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import db
from tumnis.core.cache import CacheKey, CacheSpec, invalidate_on_commit, register_cache
from tumnis.core.crypto import MasterKeys

Kind = Literal["tmn", "tmt", "tmd"]
KINDS: Final[tuple[Kind, ...]] = ("tmn", "tmt", "tmd")
KEY_RE: Final = re.compile(r"^(tmn|tmt|tmd)_([a-z2-7]{12})_([A-Za-z0-9_-]{43})$")
PREFIX_BYTES: Final = 8  # 13 base32 characters, cut to 12 (60 bits)
PREFIX_LENGTH: Final = 12
SECRET_BYTES: Final = 32  # token_urlsafe(32): 43 characters

CACHE_NAME: Final = "auth.api_key_by_prefix"
KEY_CACHE = register_cache(
    CacheSpec(
        CACHE_NAME,
        "system",
        60.0,  # plan default; expiry is checked on every use, so the TTL only bounds staleness
        (
            "api_keys: create, rotate, revoke (invalidate_on_commit)",
            "task_tokens: revoke_task_tokens_for_run",
            "device_tokens: reissue revokes the previous token",
        ),
    )
)


@dataclass(frozen=True)
class NewSecret:
    display: str  # "tmn_<prefix>_<secret>", returned once
    prefix: str
    secret_hmac: bytes
    pepper_version: int


@dataclass(frozen=True)
class ParsedSecret:
    kind: Kind
    prefix: str
    secret: str


@dataclass(frozen=True)
class KeyRow:
    """One row a prefix resolves to (an API key, or a task or device token)."""

    key_id: UUID
    workspace_id: UUID
    secret_hmac: bytes
    pepper_version: int
    scopes: frozenset[str]
    project_ids: frozenset[UUID] | None  # None = every project
    expires_at: datetime | None
    revoked_at: datetime | None
    subject_id: UUID | None = None  # task tokens: the run; device tokens: the runner

    def to_json(self) -> dict[str, Any]:
        return {
            "key_id": str(self.key_id),
            "workspace_id": str(self.workspace_id),
            "secret_hmac": base64.b64encode(self.secret_hmac).decode(),
            "pepper_version": self.pepper_version,
            "scopes": sorted(self.scopes),
            "project_ids": None
            if self.project_ids is None
            else sorted(str(p) for p in self.project_ids),
            "expires_at": None if self.expires_at is None else self.expires_at.isoformat(),
            "revoked_at": None if self.revoked_at is None else self.revoked_at.isoformat(),
            "subject_id": None if self.subject_id is None else str(self.subject_id),
        }

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> Self:
        def when(raw: str | None) -> datetime | None:
            return None if raw is None else datetime.fromisoformat(raw)

        projects = value["project_ids"]
        return cls(
            key_id=UUID(value["key_id"]),
            workspace_id=UUID(value["workspace_id"]),
            secret_hmac=base64.b64decode(value["secret_hmac"]),
            pepper_version=int(value["pepper_version"]),
            scopes=frozenset(value["scopes"]),
            project_ids=None if projects is None else frozenset(UUID(p) for p in projects),
            expires_at=when(value["expires_at"]),
            revoked_at=when(value["revoked_at"]),
            subject_id=None if value["subject_id"] is None else UUID(value["subject_id"]),
        )


def parse(display: str) -> ParsedSecret | None:
    """The parts of a displayed secret, or None when it is not one of ours."""
    match = KEY_RE.fullmatch(display)
    if match is None:
        return None
    kind, prefix, secret = match.groups()
    return ParsedSecret(cast("Kind", kind), prefix, secret)


def hmac_secret(pepper: bytes, secret: str) -> bytes:
    return hmac.new(pepper, secret.encode(), hashlib.sha256).digest()


def generate(kind: Kind, peppers: MasterKeys) -> NewSecret:
    prefix = base64.b32encode(secrets.token_bytes(PREFIX_BYTES)).decode().lower().rstrip("=")
    prefix = prefix[:PREFIX_LENGTH]
    secret = secrets.token_urlsafe(SECRET_BYTES)
    version = peppers.active
    return NewSecret(
        f"{kind}_{prefix}_{secret}", prefix, hmac_secret(peppers.keys[version], secret), version
    )


def verify(candidate_secret: str, rows: Sequence[KeyRow], peppers: MasterKeys) -> KeyRow | None:
    """Checks every row with the prefix (normally one) with hmac.compare_digest; no early
    exit on a match. A row made with a pepper this process no longer has never matches."""
    found = None
    for row in rows:
        pepper = peppers.keys.get(row.pepper_version)
        expected = hmac_secret(pepper, candidate_secret) if pepper is not None else b""
        if hmac.compare_digest(expected, row.secret_hmac) and pepper is not None:
            found = row
    return found


# --- Lookup and cache --------------------------------------------------------------------

_RESOLVE_KEY: Final = text(
    "SELECT key_id, workspace_id, secret_hmac, pepper_version, scopes, project_ids,"
    " expires_at, revoked_at FROM app.auth_resolve_api_key(:prefix)"
)
_RESOLVE_TOKEN: Final = text(
    "SELECT token_id, workspace_id, token_hmac, pepper_version, scopes, project_ids,"
    " expires_at, revoked_at, subject_id FROM app.auth_resolve_token(:kind, :prefix)"
)
_TOKEN_KIND: Final[dict[Kind, str]] = {"tmt": "task", "tmd": "device"}


def cache_key(kind: Kind, prefix: str) -> CacheKey:
    return CacheKey.system(CACHE_NAME, kind, prefix)


def _row(raw: Any) -> KeyRow:
    projects = raw.project_ids
    return KeyRow(
        key_id=raw[0],
        workspace_id=raw.workspace_id,
        secret_hmac=bytes(raw[2]),
        pepper_version=raw.pepper_version,
        scopes=frozenset(raw.scopes or ()),
        project_ids=None if projects is None else frozenset(projects),
        expires_at=raw.expires_at,
        revoked_at=raw.revoked_at,
        subject_id=getattr(raw, "subject_id", None),
    )


async def _query(kind: Kind, prefix: str) -> list[KeyRow]:
    async with db.app_sessionmaker()() as s, s.begin():
        if kind == "tmn":
            result = await s.execute(_RESOLVE_KEY, {"prefix": prefix})
        else:
            result = await s.execute(_RESOLVE_TOKEN, {"kind": _TOKEN_KIND[kind], "prefix": prefix})
        return [_row(raw) for raw in result]


async def lookup(kind: Kind, prefix: str) -> list[KeyRow]:
    """The rows a prefix resolves to, through the cache (an empty answer is cached too, so
    guessing prefixes costs no database round trips)."""
    key = cache_key(kind, prefix)
    cached = await KEY_CACHE.get(key)
    if cached is not None:
        return [KeyRow.from_json(item) for item in json.loads(cached)]
    token = KEY_CACHE.token()
    rows = await _query(kind, prefix)
    payload = json.dumps([row.to_json() for row in rows]).encode()
    await KEY_CACHE.fill(key, payload, since=token)
    return rows


async def invalidate(session: AsyncSession, kind: Kind, *prefixes: str | None) -> None:
    """Drop the cached lookups of these prefixes in every process when `session` commits."""
    for prefix in prefixes:
        if prefix:
            await invalidate_on_commit(session, cache_key(kind, prefix))
