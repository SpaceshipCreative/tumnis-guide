"""`KeyCapabilityChecker`: what a linked bucket's key may do, asked of its provider (P3-13,
FR-15.11, SEC-5).

- Backblaze B2: `GET https://api.backblazeb2.com/b2api/v4/b2_authorize_account` with HTTP
  Basic auth of `keyId:applicationKey` (https://www.backblaze.com/apidocs/b2-authorize-account);
  the key's capabilities, bucket and name prefix are `apiInfo.storageApi.allowed`.
- MinIO: the admin "account info" call for the key's own account,
  `GET <endpoint>/minio/admin/v3/accountinfo`, signed with Signature V4 (service `s3`)
  like any S3 request. A key without admin rights may call it for itself; the answer
  carries the key's own policy document (`Policy`, an object or a JSON string).

Both go through `core.net.guarded_client`, so the SSRF guard checks the host and the
connection goes to the checked address. A 401 or 403 (the provider refused the key) is
`AdapterRejected`; anything the caller cannot read is `AdapterRejected` too, so the api
can tell the user to connect the source unchecked instead.
"""

import base64
import json
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, Final

import httpx
from botocore.auth import S3SigV4Auth  # type: ignore[import-untyped]
from botocore.awsrequest import AWSRequest  # type: ignore[import-untyped]
from botocore.credentials import Credentials  # type: ignore[import-untyped]

from tumnis.core.adapters.base import Adapter, CallPolicy
from tumnis.core.adapters.errors import AdapterRejected, AdapterUnavailable
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.net import NetPolicy, Resolver, guarded_client, system_resolver
from tumnis.modules.knowledge.adapters.s3 import endpoint_policy
from tumnis.modules.knowledge.rules import (
    KeyCapabilities,
    b2_key_capabilities,
    minio_key_capabilities,
)

B2_AUTHORIZE_URL: Final = "https://api.backblazeb2.com/b2api/v4/b2_authorize_account"
MINIO_ACCOUNT_INFO: Final = "/minio/admin/v3/accountinfo"
POLICY: Final = CallPolicy(timeout_s=15.0)  # plan default for a one-off check at connect
TIMEOUT_S: Final = 10.0
_REFUSED: Final = frozenset({401, 403})


def _json(response: httpx.Response, op: str) -> Mapping[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise AdapterRejected(KeyCapabilityChecker.name, op, "not_json") from exc
    if not isinstance(data, Mapping):
        raise AdapterRejected(KeyCapabilityChecker.name, op, "unexpected_answer")
    return data


def _policy_document(raw: object) -> Mapping[str, object] | None:
    """MinIO's `Policy`: the document itself, or the document as a JSON string."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    return raw if isinstance(raw, Mapping) else None


class KeyCapabilityChecker(Adapter):
    name: ClassVar[str] = "knowledge.key_capabilities"

    def __init__(
        self,
        *,
        net: NetPolicy,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(policy=POLICY, clock=clock or SystemClock())
        self._net = net
        self._resolver = resolver
        self._transport = transport

    async def _get(
        self, op: str, url: str, headers: Mapping[str, str], policy: NetPolicy
    ) -> httpx.Response:
        async def fn() -> httpx.Response:
            try:
                async with guarded_client(
                    policy, timeout=TIMEOUT_S, resolver=self._resolver, inner=self._transport
                ) as client:
                    response = await client.get(url, headers=dict(headers))
            except httpx.HTTPError as exc:
                raise AdapterUnavailable(self.name, op, type(exc).__name__) from exc
            if response.status_code in _REFUSED:
                raise AdapterRejected(self.name, op, "key_rejected")
            if response.status_code >= 500 or response.status_code == 429:  # noqa: PLR2004
                raise AdapterUnavailable(self.name, op, str(response.status_code))
            if response.status_code != 200:  # noqa: PLR2004
                raise AdapterRejected(self.name, op, str(response.status_code))
            return response

        return await self.call(op, fn, idempotent=True)

    async def check_b2(self, key_id: str, application_key: str, *, bucket: str) -> KeyCapabilities:
        token = base64.b64encode(f"{key_id}:{application_key}".encode()).decode()
        response = await self._get(
            "b2_authorize_account", B2_AUTHORIZE_URL, {"Authorization": f"Basic {token}"}, self._net
        )
        data = _json(response, "b2_authorize_account")
        allowed = data.get("apiInfo", {}).get("storageApi", {}).get("allowed")
        if not isinstance(allowed, Mapping):
            raise AdapterRejected(self.name, "b2_authorize_account", "no_allowed")
        return b2_key_capabilities(allowed, bucket)

    async def check_minio(  # where, who, and what the key is for
        self,
        endpoint: str,
        region: str,
        access_key: str,
        secret_key: str,
        *,
        bucket: str,
        prefixes: Sequence[str],
    ) -> KeyCapabilities:
        url = endpoint.rstrip("/") + MINIO_ACCOUNT_INFO
        request = AWSRequest(method="GET", url=url, data=b"")
        S3SigV4Auth(Credentials(access_key, secret_key), "s3", region).add_auth(request)
        headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
        policy = endpoint_policy(self._net, endpoint)
        response = await self._get("minio_account_info", url, headers, policy)
        data = _json(response, "minio_account_info")
        document = _policy_document(data.get("Policy"))
        return minio_key_capabilities(document, bucket, prefixes)
