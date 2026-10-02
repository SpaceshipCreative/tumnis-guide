"""S3 buckets as linked knowledge sources (P3-13, FR-15.11, SEC-5): connecting one, and
MinIO's bucket notifications. `api.py` re-exports what other modules and the router use.

Connecting (`create_s3_source`) checks, in this order, before anything is saved: the
bucket name and the prefixes; the endpoint through the SSRF guard (`ssrf_blocked`, nothing
sent); https in hosted mode; a B2 master key id (refused); then the key's capabilities,
where the provider lets Tumnis ask (B2: `b2_authorize_account`; MinIO: account info), and
`rules.capabilities_acceptable` (a key that can write or delete, or is not limited to the
bucket, is refused with 422 `key_not_read_only`; other providers are accepted with the
UNVERIFIED_KEY warning). Only then are the connection row and the `s3_sources` row made:
the keys sealed with the workspace data key (aad `s3_sources:<id>`), the webhook token
kept as its SHA-256 and shown once.

Connection rows: until P3-02's `create_connection` lands, the row comes from
`integrations.seed_connection` (kind `knowledge`, provider `s3`, account `s3:<uuid>`),
behind the one seam `_new_connection`.

Notifications (`accept_minio_notification`): MinIO's webhook target sends its configured
`auth_token` as `Authorization: Bearer <token>` (minio/minio
internal/event/target/webhook.go) and does not sign the body, so the token stands in for
the signature the PRD asks for (plan deviation, flagged for Scott). The body is never
trusted: each record only queues a re-check (a HEAD) of the key it names in the
connection's own bucket (`knowledge_s3_source_recheck` on the `sync` queue).
"""

import dataclasses
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Final, Literal
from urllib.parse import unquote_plus
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import RowMapping, Table, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import db, deadletter, settings_store
from tumnis.core.adapters.errors import AdapterError
from tumnis.core.errors import ProblemError
from tumnis.core.ids import uuid7
from tumnis.core.net import NetPolicy, Resolver, SsrfBlocked, resolve_and_check, system_resolver
from tumnis.core.versioning import NotFound
from tumnis.modules.integrations import api as integrations
from tumnis.modules.knowledge.adapters.port import KeyCapabilityCheck, S3SourceReader
from tumnis.modules.knowledge.adapters.s3 import (
    S3Config,
    endpoint_host_port,
    endpoint_needs_https,
    endpoint_policy,
)
from tumnis.modules.knowledge.adapters.s3_source.capability import KeyCapabilityChecker
from tumnis.modules.knowledge.adapters.s3_source.connector import S3SourceConnector
from tumnis.modules.knowledge.models import S3Source
from tumnis.modules.knowledge.rules import (
    UNCHECKED,
    KeyCapabilities,
    PathRejected,
    capabilities_acceptable,
    looks_like_b2_master_key_id,
    normalize_prefix,
)
from tumnis.modules.knowledge.storage import safe_prefix
from tumnis.modules.projects import api as projects

PROVIDER: Final = "s3"  # connections.provider of a linked bucket
RECHECK_WORKFLOW: Final = "knowledge_s3_source_recheck"
SYNC_WORKFLOW: Final = "knowledge_s3_source_sync"
SYNC_QUEUE: Final = "sync"
MAX_RECORDS: Final = 100  # records one notification may queue re-checks for (plan default)
MAX_PREFIXES: Final = 50
_BUCKET: Final = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_BEARER: Final = "Bearer "

_sources: Table = S3Source.__table__  # type: ignore[assignment]

Provider = Literal["minio", "b2", "other"]


class S3PrefixMap(BaseModel):
    """A bucket prefix and the project its files go to (None: the workspace knowledge
    base)."""

    prefix: str = Field(max_length=500)
    project_id: UUID | None = None


class S3SourceIn(BaseModel):
    provider: Provider  # minio and b2 have their key checked; other is accepted unchecked
    endpoint: str = Field(max_length=500)  # http(s)://host[:port]
    region: str = Field(default="us-east-1", max_length=64)
    bucket: str = Field(max_length=63)
    access_key: str = Field(min_length=1, max_length=256)
    secret_key: str = Field(min_length=1, max_length=256)  # write-only: sealed, never answered
    path_style: bool = True
    trusted: bool = False  # files come in trusted and untainted (FR-15.11)
    prefixes: list[S3PrefixMap] = Field(min_length=1, max_length=MAX_PREFIXES)


class S3SourceOut(BaseModel):
    id: UUID  # the connection's id: what the webhook path names
    provider: Provider
    endpoint: str
    region: str
    bucket: str
    path_style: bool
    trusted: bool
    prefixes: list[S3PrefixMap]
    capabilities: KeyCapabilities
    warning: str | None  # UNVERIFIED_KEY when the provider gives no check
    last_sync_at: datetime | None
    version: int


class S3SourceCreated(S3SourceOut):
    """The source as created, with its MinIO webhook token: shown once, never stored."""

    webhook_token: str
    webhook_path: str
    minio_commands: list[str]


class NotificationAccepted(BaseModel):
    status: Literal["accepted"] = "accepted"
    queued: int


# --- Test seams ---------------------------------------------------------------------------

ReaderFactory = Callable[[S3Config, str, NetPolicy, Resolver], S3SourceReader]
CheckerFactory = Callable[[NetPolicy, Resolver], KeyCapabilityCheck]


def _real_reader(config: S3Config, bucket: str, net: NetPolicy, resolver: Resolver) -> Any:
    return S3SourceConnector(config, bucket=bucket, net_policy=net, resolver=resolver)


def _real_checker(net: NetPolicy, resolver: Resolver) -> KeyCapabilityCheck:
    return KeyCapabilityChecker(net=net, resolver=resolver)


class _Seams:
    # A linked bucket is someone else's: there is no Tumnis-side world for a fakes-mode
    # stack to keep it in, so the real adapters are the default in every mode and a test
    # swaps in `FakeS3Source` or `FakeKeyCapabilities` here.
    reader: ReaderFactory = _real_reader
    checker: CheckerFactory = _real_checker


def use_s3_adapters(
    *, reader: ReaderFactory | None = None, checker: CheckerFactory | None = None
) -> tuple[ReaderFactory, CheckerFactory]:
    """Swap how sources are read and keys checked (None: the real adapter); returns the
    previous pair."""
    previous = (_Seams.reader, _Seams.checker)
    _Seams.reader = reader or _real_reader
    _Seams.checker = checker or _real_checker
    return previous


# --- Connecting -----------------------------------------------------------------------------


def _aad(source_id: UUID) -> bytes:
    return f"s3_sources:{source_id}".encode()


def _bad(code: str, detail: str) -> ProblemError:
    return ProblemError(422, code, detail)


def _prefixes(body: S3SourceIn) -> list[S3PrefixMap]:
    found: dict[str, S3PrefixMap] = {}
    for item in body.prefixes:
        prefix = normalize_prefix(item.prefix)
        try:
            safe_prefix(prefix)
        except PathRejected as exc:
            raise _bad("invalid_prefix", f"{item.prefix!r} is not a safe prefix.") from exc
        if prefix in found:
            raise _bad("invalid_prefix", f"{prefix!r} is mapped twice.")
        found[prefix] = S3PrefixMap(prefix=prefix, project_id=item.project_id)
    return list(found.values())


async def _check_endpoint(url: str, net: NetPolicy, resolver: Resolver) -> None:
    """The SSRF guard on the endpoint before anything is sent (SEC-5)."""
    try:
        host, port = endpoint_host_port(url)
        await resolve_and_check(host, port, endpoint_policy(net, url), resolver)
    except ValueError as exc:
        raise _bad("invalid_source", "The endpoint is not an http(s) URL.") from exc
    except SsrfBlocked as exc:
        raise _bad("ssrf_blocked", "That endpoint is not allowed.") from exc
    except AdapterError as exc:
        raise _bad("invalid_source", "The endpoint does not resolve.") from exc
    if endpoint_needs_https(net, url):
        raise _bad("invalid_source", "Hosted Tumnis needs an https endpoint.")


async def _capabilities(
    body: S3SourceIn, prefixes: Sequence[str], net: NetPolicy, resolver: Resolver
) -> KeyCapabilities:
    if body.provider == "other":
        return UNCHECKED
    checker = _Seams.checker(net, resolver)
    try:
        if body.provider == "b2":
            return await checker.check_b2(body.access_key, body.secret_key, bucket=body.bucket)
        return await checker.check_minio(
            body.endpoint,
            body.region,
            body.access_key,
            body.secret_key,
            bucket=body.bucket,
            prefixes=prefixes,
        )
    except SsrfBlocked as exc:
        raise _bad("ssrf_blocked", "That endpoint is not allowed.") from exc
    except AdapterError as exc:
        raise _bad(
            "capability_check_failed",
            "Tumnis could not check this key with the provider. Check the key, or connect"
            " the bucket as provider 'other' to skip the check.",
        ) from exc


async def _new_connection(s: AsyncSession) -> UUID:
    """The connection row the source hangs off. Seam: P3-02's `create_connection` replaces
    `seed_connection` here once it lands."""
    return await integrations.seed_connection(s, "knowledge", PROVIDER, f"s3:{uuid7()}")


def _out(row: Mapping[Any, Any]) -> S3SourceOut:
    caps = KeyCapabilities.model_validate(row["capabilities"] or {"checked": False})
    _, warning = capabilities_acceptable(caps)
    return S3SourceOut(
        id=row["connection_id"],
        provider=row["provider"],
        endpoint=row["endpoint"],
        region=row["region"],
        bucket=row["bucket"],
        path_style=row["path_style"],
        trusted=row["trusted"],
        prefixes=[S3PrefixMap.model_validate(p) for p in row["prefixes"]],
        capabilities=caps,
        warning=warning,
        last_sync_at=row["last_sync_at"],
        version=row["version"],
    )


def minio_commands(connection_id: UUID, token: str, bucket: str, base_url: str) -> list[str]:
    """What the user runs once to send the bucket's events to Tumnis (`mc` 2024+)."""
    target = f"tumnis{connection_id.hex[:8]}"
    endpoint = f"{base_url.rstrip('/')}/v1/webhooks/minio/{connection_id}"
    return [
        f"mc admin config set ALIAS notify_webhook:{target} endpoint={endpoint} auth_token={token}",
        "mc admin service restart ALIAS",
        f"mc event add ALIAS/{bucket} arn:minio:sqs::{target}:webhook --event put,delete",
    ]


async def create_s3_source(  # the session, the form, and the SSRF policy
    s: AsyncSession,
    body: S3SourceIn,
    *,
    net: NetPolicy,
    resolver: Resolver = system_resolver,
    base_url: str = "",
) -> S3SourceCreated:
    if not _BUCKET.match(body.bucket):
        raise _bad("invalid_source", "The bucket name is not valid.")
    prefixes = _prefixes(body)
    await _check_endpoint(body.endpoint, net, resolver)
    if body.provider == "b2" and looks_like_b2_master_key_id(body.access_key):
        raise _bad("b2_master_key", "Use a B2 application key, never the account's master key.")
    for item in prefixes:
        if item.project_id is not None and not await projects.project_exists(s, item.project_id):
            raise NotFound("projects", item.project_id)
    caps = await _capabilities(body, [p.prefix for p in prefixes], net, resolver)
    accepted, reason = capabilities_acceptable(caps)
    if not accepted:
        raise _bad("key_not_read_only", f"Use a read-only key for this bucket ({reason}).")
    connection_id = await _new_connection(s)
    source_id = uuid7()
    workspace_id = await s.scalar(text("SELECT app.current_workspace_id()"))
    config = S3Config(
        endpoint=body.endpoint,
        region=body.region,
        access_key=body.access_key,
        secret_key=body.secret_key,
        path_style=body.path_style,
    )
    blob = json.dumps(dataclasses.asdict(config)).encode()
    _, sealed = await settings_store.seal_for_workspace(s, workspace_id, blob, aad=_aad(source_id))
    token = secrets.token_urlsafe(32)
    row = (
        (
            await s.execute(
                _sources.insert()
                .values(
                    id=source_id,
                    connection_id=connection_id,
                    provider=body.provider,
                    endpoint=body.endpoint,
                    region=body.region,
                    bucket=body.bucket,
                    path_style=body.path_style,
                    config_enc=sealed,
                    prefixes=[p.model_dump(mode="json") for p in prefixes],
                    trusted=body.trusted,
                    capabilities=caps.model_dump(mode="json"),
                    webhook_token_sha256=hashlib.sha256(token.encode()).digest(),
                )
                .returning(*_sources.c)
            )
        )
        .mappings()
        .one()
    )
    out = _out(row)
    return S3SourceCreated(
        **out.model_dump(),
        webhook_token=token,
        webhook_path=f"/v1/webhooks/minio/{connection_id}",
        minio_commands=minio_commands(connection_id, token, body.bucket, base_url),
    )


async def source_row(s: AsyncSession, connection_id: UUID) -> RowMapping:
    row = (
        (
            await s.execute(
                select(_sources).where(
                    _sources.c.connection_id == connection_id, _sources.c.deleted_at.is_(None)
                )
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("s3_sources", connection_id)
    return row


async def list_s3_sources(s: AsyncSession) -> list[S3SourceOut]:
    rows = (
        (
            await s.execute(
                select(_sources).where(_sources.c.deleted_at.is_(None)).order_by(_sources.c.id)
            )
        )
        .mappings()
        .all()
    )
    return [_out(row) for row in rows]


async def get_s3_source(s: AsyncSession, connection_id: UUID) -> S3SourceOut:
    return _out(await source_row(s, connection_id))


async def delete_s3_source(s: AsyncSession, connection_id: UUID) -> None:
    """Stop syncing the bucket: the source is soft-deleted and its webhook token stops
    working. Its documents stay (the user trashes them like any other)."""
    row = await source_row(s, connection_id)
    await s.execute(
        update(_sources).where(_sources.c.id == row["id"]).values(deleted_at=text("now()"))
    )


async def open_source(s: AsyncSession, row: Mapping[Any, Any], *, net: NetPolicy) -> S3SourceReader:
    """A reader on the source's bucket (the caller closes it with `aclose`)."""
    blob = await settings_store.open_for_workspace(
        s, row["workspace_id"], row["config_enc"], aad=_aad(row["id"])
    )
    data = json.loads(blob)
    config = S3Config(
        endpoint=data["endpoint"],
        region=data["region"],
        access_key=data["access_key"],
        secret_key=data["secret_key"],
        path_style=data["path_style"],
    )
    return _Seams.reader(config, row["bucket"], net, system_resolver)


# --- MinIO notifications ----------------------------------------------------------------------


def _invalid_token() -> ProblemError:
    return ProblemError(401, "invalid_token", "The notification's token is not valid.")


def notification_keys(body: bytes) -> list[str]:
    """The object keys a MinIO notification names (`Records[].s3.object.key`, query-
    escaped by MinIO), at most MAX_RECORDS, each once; the body's bucket is ignored."""
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ProblemError(422, "invalid_notification", "The body is not JSON.") from exc
    records = data.get("Records") if isinstance(data, Mapping) else None
    if not isinstance(records, list):
        raise ProblemError(422, "invalid_notification", "The body has no Records.")
    keys: list[str] = []
    for record in records[:MAX_RECORDS]:
        try:
            raw = record["s3"]["object"]["key"]
        except (KeyError, TypeError):
            continue
        if isinstance(raw, str) and raw:
            key = unquote_plus(raw)
            if key not in keys:
                keys.append(key)
    return keys


async def accept_minio_notification(
    connection_id: UUID, authorization: str | None, body: bytes
) -> NotificationAccepted:
    """Check the bearer token against the source's (constant time; an unknown or deleted
    source is the same 401), then queue one re-check per key named."""
    async with db.app_sessionmaker()() as s, s.begin():
        found = (
            await s.execute(
                text("SELECT workspace_id, webhook_token_sha256 FROM app.s3_source_webhook(:c)"),
                {"c": connection_id},
            )
        ).first()
    presented = (authorization or "").strip()
    if not presented.startswith(_BEARER):
        raise _invalid_token()
    digest = hashlib.sha256(presented[len(_BEARER) :].strip().encode()).digest()
    expected = bytes(found.webhook_token_sha256) if found is not None else bytes(32)
    if not hmac.compare_digest(digest, expected) or found is None:
        raise _invalid_token()
    keys = notification_keys(body)
    client = deadletter.dbos_client()
    for key in keys:
        await client.enqueue_async(
            {
                "queue_name": SYNC_QUEUE,
                "workflow_name": RECHECK_WORKFLOW,
                "workflow_id": f"s3-recheck:{connection_id}:{uuid7()}",
            },
            str(found.workspace_id),
            str(connection_id),
            key,
        )
    return NotificationAccepted(queued=len(keys))
