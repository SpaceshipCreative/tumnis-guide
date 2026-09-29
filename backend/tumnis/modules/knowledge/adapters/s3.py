"""`S3Storage`: MinIO, B2 or AWS through aioboto3 (P1-14, FR-15.7, SEC-5).

Keys are the location's prefix plus a `safe_rel_path`. Writes never overwrite blindly:
where the provider honours conditional puts (`capabilities.conditional_put`, from
`probe_conditional_writes` at location save) a create sends `If-None-Match: *` and a
replace `If-Match: "<etag>"`, and a 409 or 412 is `PreconditionFailed`; elsewhere a HEAD
just before the put checks the same thing (a narrow race the sync engine's scan catches).
Files are at most 50 MiB, so every write is one spooled PUT.

aioboto3 brings its own HTTP stack, so the SSRF guard runs twice: the endpoint is checked
with `resolve_and_check` when the client is made, and every connection resolves through
`_GuardedResolver`, which connects only to the address the check returned (IP literals
skip aiohttp's resolver, and the first check already covered them).
"""

import asyncio
import builtins
import socket
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from typing import Any, ClassVar, Final, Literal
from urllib.parse import urlsplit

import aioboto3  # type: ignore[import-untyped]
import aiohttp
from aiobotocore.config import AioConfig  # type: ignore[import-untyped]
from aiohttp.abc import AbstractResolver, ResolveResult
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
from pydantic import BaseModel

from tumnis.core.adapters.base import Adapter, CallPolicy
from tumnis.core.adapters.errors import AdapterError, AdapterRejected, AdapterUnavailable
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.net import NetPolicy, Resolver, resolve_and_check, system_resolver
from tumnis.modules.knowledge.rules import PathRejected, etag_equal, safe_rel_path
from tumnis.modules.knowledge.storage import (
    LIST_PAGE_SIZE,
    FileStat,
    Health,
    NotFound,
    Page,
    PreconditionFailed,
    StorageError,
    call_storage,
    safe_prefix,
    spool,
)

Answer = Literal["precondition_failed", "ignored"]

CHUNK: Final = 64 * 1024
POLICY: Final = CallPolicy(timeout_s=120.0)  # a 50 MiB put on a slow link; plan default
S3_PORTS: Final = frozenset({9000})  # MinIO's usual port, beside the default web ports
TUMNIS_KEYS: Final = ".tumnis/"  # health and probe objects; never listed
_STALE_ETAG: Final = '"' + "0" * 32 + '"'


@dataclass(frozen=True)
class S3Config:
    endpoint: str | None  # None: AWS itself
    region: str
    access_key: str
    secret_key: str
    path_style: bool = True
    sse: Literal["AES256"] | None = None

    def endpoint_url(self) -> str:
        return self.endpoint or f"https://s3.{self.region}.amazonaws.com"


class ConditionalWriteProbe(BaseModel, frozen=True):
    if_none_match_star: Answer
    stale_if_match: Answer

    @property
    def conditional_put(self) -> bool:
        return self.if_none_match_star == self.stale_if_match == "precondition_failed"


def endpoint_host_port(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"{url!r} is not an http(s) endpoint")
    return parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)


def endpoint_policy(policy: NetPolicy, url: str) -> NetPolicy:
    """The ports an S3 endpoint may use: the web ports, MinIO's 9000, and in self-hosted
    mode whatever port the endpoint names (a homelab MinIO on its own port)."""
    ports = policy.ports | S3_PORTS
    explicit = urlsplit(url).port
    if policy.mode == "self-hosted" and explicit is not None:
        ports |= {explicit}
    return replace(policy, ports=ports)


class _GuardedResolver(AbstractResolver):
    """aiohttp's resolver for the S3 client: every name is resolved and checked by
    `resolve_and_check`, and the connection goes to the checked address only."""

    def __init__(self, policy: NetPolicy, resolver: Resolver) -> None:
        self._policy = policy
        self._resolver = resolver

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> builtins.list[ResolveResult]:
        address = await resolve_and_check(host, port, self._policy, self._resolver)
        return [
            ResolveResult(
                hostname=host,
                host=str(address),
                port=port,
                family=socket.AF_INET6 if address.version == 6 else socket.AF_INET,  # noqa: PLR2004
                proto=0,
                flags=socket.AI_NUMERICHOST,
            )
        ]

    async def close(self) -> None:
        return None


def _status(exc: ClientError) -> int:
    meta = exc.response.get("ResponseMetadata", {})
    return int(meta.get("HTTPStatusCode") or 0)


def _retry_after(exc: ClientError) -> float | None:
    headers = exc.response.get("ResponseMetadata", {}).get("HTTPHeaders", {})
    try:
        return float(headers["retry-after"])
    except (KeyError, ValueError):
        return None


def _etag(raw: str) -> str:
    return raw.strip('"')


class S3Storage(Adapter):
    name: ClassVar[str] = "knowledge.s3"

    def __init__(
        self,
        config: S3Config,
        *,
        bucket: str,
        prefix: str = "",
        conditional_put: bool = True,
        health_write: bool = False,
        net_policy: NetPolicy | None = None,
        resolver: Resolver = system_resolver,
        clock: Clock | None = None,
        policy: CallPolicy | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        super().__init__(policy=policy or POLICY, clock=self._clock)
        self.config = config
        self.bucket = bucket
        self.prefix = safe_prefix(prefix.strip("/") + "/") if prefix.strip("/") else ""
        self.conditional_put = conditional_put
        self.health_write = health_write
        self._net = net_policy
        self._resolver = resolver
        self._stack: AsyncExitStack | None = None
        self._client: Any = None
        self._lock = asyncio.Lock()

    # --- Client --------------------------------------------------------------------------

    async def _s3(self) -> Any:
        """The client, made on first use and kept until `aclose` (one per location)."""
        async with self._lock:
            if self._client is None:
                url = self.config.endpoint_url()
                connector: dict[str, Any] = {}
                if self._net is not None:
                    policy = endpoint_policy(self._net, url)
                    host, port = endpoint_host_port(url)
                    await resolve_and_check(host, port, policy, self._resolver)
                    connector["resolver"] = _GuardedResolver(policy, self._resolver)
                config = AioConfig(
                    s3={"addressing_style": "path" if self.config.path_style else "auto"},
                    signature_version="s3v4",
                    retries={"max_attempts": 1},  # the adapter retries
                    connect_timeout=5,
                    read_timeout=30,
                    connector_args=connector or None,
                )
                stack = AsyncExitStack()
                self._client = await stack.enter_async_context(
                    aioboto3.Session().client(
                        "s3",
                        endpoint_url=url,
                        region_name=self.config.region,
                        aws_access_key_id=self.config.access_key,
                        aws_secret_access_key=self.config.secret_key,
                        config=config,
                    )
                )
                self._stack = stack
            return self._client

    async def aclose(self) -> None:
        async with self._lock:
            stack, self._stack, self._client = self._stack, None, None
        if stack is not None:
            await stack.aclose()

    def _key(self, rel: str) -> str:
        return self.prefix + rel

    async def _call[T](self, op: str, fn: Callable[[], Awaitable[T]], *, idempotent: bool) -> T:
        """`fn` through the adapter's timeout and breaker, botocore errors translated."""

        async def attempt() -> T:
            try:
                return await fn()
            except (StorageError, AdapterError):
                raise
            except ClientError as exc:
                status = _status(exc)
                code = exc.response.get("Error", {}).get("Code", str(status))
                if status >= 500 or status == 429:  # noqa: PLR2004
                    raise AdapterUnavailable(
                        self.name, op, code, retry_after_s=_retry_after(exc)
                    ) from exc
                raise AdapterRejected(self.name, op, code) from exc
            except (BotoCoreError, aiohttp.ClientError, OSError) as exc:
                raise AdapterUnavailable(self.name, op, type(exc).__name__) from exc

        return await call_storage(self, op, attempt, idempotent=idempotent)

    # --- Files ---------------------------------------------------------------------------

    async def _head(self, rel: str) -> FileStat | None:
        s3 = await self._s3()
        try:
            r = await s3.head_object(Bucket=self.bucket, Key=self._key(rel))
        except ClientError as exc:
            if _status(exc) == 404:  # noqa: PLR2004
                return None
            raise
        return FileStat(
            path=rel, size=r["ContentLength"], mtime=r["LastModified"], etag=_etag(r["ETag"])
        )

    async def stat(self, path: str) -> FileStat | None:
        rel = safe_rel_path(path)
        return await self._call("stat", lambda: self._head(rel), idempotent=True)

    async def list(self, prefix: str, cursor: str | None) -> Page[FileStat]:
        safe = safe_prefix(prefix)

        async def fn() -> Page[FileStat]:
            s3 = await self._s3()
            kw: dict[str, Any] = {
                "Bucket": self.bucket,
                "Prefix": self.prefix + safe,
                "MaxKeys": LIST_PAGE_SIZE,
            }
            if cursor:
                kw["ContinuationToken"] = cursor
            r = await s3.list_objects_v2(**kw)
            items = []
            for obj in r.get("Contents", []):
                key = obj["Key"][len(self.prefix) :]
                if key.startswith(TUMNIS_KEYS):
                    continue
                try:
                    rel = safe_rel_path(key)
                except PathRejected:
                    continue  # a key Tumnis cannot address; the sync engine reports it
                if rel != key:
                    continue
                items.append(
                    FileStat(
                        path=rel,
                        size=obj["Size"],
                        mtime=obj["LastModified"],
                        etag=_etag(obj["ETag"]),
                    )
                )
            more = r.get("IsTruncated") and r.get("NextContinuationToken")
            return Page[FileStat](items=items, next_cursor=more or None)

        return await self._call("list", fn, idempotent=True)

    async def read(self, path: str) -> AsyncIterator[bytes]:
        rel = safe_rel_path(path)

        async def open_body() -> Any:
            s3 = await self._s3()
            try:
                r = await s3.get_object(Bucket=self.bucket, Key=self._key(rel))
            except ClientError as exc:
                if _status(exc) == 404:  # noqa: PLR2004
                    raise NotFound(rel) from None
                raise
            return r["Body"]

        body = await self._call("read", open_body, idempotent=True)
        try:
            async for chunk in body.iter_chunks(CHUNK):
                yield chunk
        except (aiohttp.ClientError, BotoCoreError) as exc:
            raise AdapterUnavailable(self.name, "read", type(exc).__name__) from exc
        finally:
            body.close()

    async def write(self, path: str, data: AsyncIterator[bytes], if_match: str | None) -> FileStat:
        rel = safe_rel_path(path)
        body = await spool(data)
        return await self._call("write", lambda: self._put(rel, body, if_match), idempotent=False)

    async def _put(self, rel: str, body: bytes, if_match: str | None) -> FileStat:
        s3 = await self._s3()
        kw: dict[str, Any] = {"Bucket": self.bucket, "Key": self._key(rel), "Body": body}
        if self.config.sse:
            kw["ServerSideEncryption"] = self.config.sse
        if self.conditional_put:
            if if_match is None:
                kw["IfNoneMatch"] = "*"
            else:
                kw["IfMatch"] = f'"{_etag(if_match)}"'
            try:
                r = await s3.put_object(**kw)
            except ClientError as exc:
                status = _status(exc)
                # A replace of a missing key answers 404 on S3 and MinIO.
                if status in (409, 412) or (if_match is not None and status == 404):  # noqa: PLR2004
                    raise PreconditionFailed(await self._head(rel)) from None
                raise
        else:
            current = await self._head(rel)  # the HEAD check just before the put
            if if_match is None:
                if current is not None:
                    raise PreconditionFailed(current)
            elif current is None or not etag_equal(current.etag, if_match):
                raise PreconditionFailed(current)
            r = await s3.put_object(**kw)
        return FileStat(path=rel, size=len(body), mtime=self._clock.now(), etag=_etag(r["ETag"]))

    async def move(self, src: str, dst: str) -> None:
        """Copy through a create-only write, then delete the source: the destination is
        never clobbered, and a crash between the two leaves both copies, not neither."""
        src_rel, dst_rel = safe_rel_path(src), safe_rel_path(dst)
        data = await spool(self.read(src_rel))
        await self.write(dst_rel, _one(data), if_match=None)
        await self.delete(src_rel)

    async def delete(self, path: str) -> None:
        rel = safe_rel_path(path)

        async def fn() -> None:
            s3 = await self._s3()
            try:
                await s3.delete_object(Bucket=self.bucket, Key=self._key(rel))
            except ClientError as exc:
                if _status(exc) != 404:  # noqa: PLR2004
                    raise

        await self._call("delete", fn, idempotent=True)

    # --- Health and probe ----------------------------------------------------------------

    async def health(self) -> Health:
        """HeadBucket; with `health_write` (the default location) also a write and delete
        of `.tumnis/health`."""

        async def fn() -> Health:
            s3 = await self._s3()
            try:
                await s3.head_bucket(Bucket=self.bucket)
            except ClientError as exc:
                if _status(exc) == 404:  # noqa: PLR2004
                    return Health.degraded("bucket_missing")
                if _status(exc) in (401, 403):
                    return Health.degraded("access_denied")
                raise
            if self.health_write:
                key = self._key(TUMNIS_KEYS + "health")
                await s3.put_object(Bucket=self.bucket, Key=key, Body=b"ok")
                await s3.delete_object(Bucket=self.bucket, Key=key)
            return Health.ok()

        try:
            return await self._call("health", fn, idempotent=True)
        except AdapterRejected as exc:
            return Health.degraded(getattr(exc, "code", None) or "rejected")
        except AdapterError:
            return Health.degraded("unreachable")

    async def probe_conditional_writes(self) -> ConditionalWriteProbe:
        """Whether this provider honours `If-None-Match: *` on an existing key and a stale
        `If-Match` (architecture open question 6): writes a `.tumnis/probe-<uuid>` object,
        tries both, and deletes it."""

        async def fn() -> ConditionalWriteProbe:
            s3 = await self._s3()
            key = self._key(f"{TUMNIS_KEYS}probe-{uuid.uuid4().hex}")
            await s3.put_object(Bucket=self.bucket, Key=key, Body=b"probe")
            try:
                star = await self._probe_put(s3, key, IfNoneMatch="*")
                stale = await self._probe_put(s3, key, IfMatch=_STALE_ETAG)
            finally:
                await s3.delete_object(Bucket=self.bucket, Key=key)
            return ConditionalWriteProbe(if_none_match_star=star, stale_if_match=stale)

        return await self._call("probe", fn, idempotent=True)

    async def _probe_put(self, s3: Any, key: str, **condition: str) -> Answer:
        try:
            await s3.put_object(Bucket=self.bucket, Key=key, Body=b"again", **condition)
        except ClientError as exc:
            if _status(exc) in (409, 412):
                return "precondition_failed"
            raise
        return "ignored"


async def _one(data: bytes) -> AsyncIterator[bytes]:
    yield data
