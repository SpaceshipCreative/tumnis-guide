"""Backblaze B2 key capabilities from `b2_authorize_account` (P3-13, FR-15.11): recorded
answers for a read-only and a read-write application key replayed through the checker's
own HTTP path (the SSRF-guarded client), so no B2 endpoint is ever called."""

from __future__ import annotations

import base64
import importlib
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from tumnis.core.net import NetPolicy, ScriptedResolver

AUTHORIZE_PATH = "/b2api/v4/b2_authorize_account"


def _replay(response: dict[str, Any], seen: list[httpx.Request]) -> httpx.MockTransport:
    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=response)

    return httpx.MockTransport(answer)


@pytest.mark.contract
@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
@pytest.mark.xfail(strict=True, reason="spec:P3-13")
async def test_recorded_b2_keys(
    recordings: Callable[[str], list[tuple[dict[str, Any], Any]]],
) -> None:
    """T-P3-13-02
    Each recorded `b2_authorize_account` answer (API v4) maps to the key's capabilities:
    a read-only key limited to the bucket (and a name prefix) is read-only and scoped; a
    read-write key can write and delete; a key with no bucket restriction is not scoped.
    The checker sends one GET to the v4 authorize path with HTTP Basic auth of
    `keyId:applicationKey`.
    """
    capability = importlib.import_module("tumnis.modules.knowledge.adapters.s3_source.capability")
    KeyCapabilityChecker = capability.KeyCapabilityChecker  # noqa: N806
    KeyCapabilities = importlib.import_module("tumnis.modules.knowledge.rules").KeyCapabilities  # noqa: N806

    cases = recordings("b2")
    assert len(cases) >= 2  # at least the read-only and read-write keys
    for raw, expected in cases:
        seen: list[httpx.Request] = []
        checker = KeyCapabilityChecker(
            net=NetPolicy(mode="self-hosted"),
            resolver=ScriptedResolver([["192.0.2.10"]]),
            transport=_replay(raw["response"], seen),
        )
        request = raw["request"]
        caps = await checker.check_b2(
            request["key_id"], request["application_key"], bucket=request["bucket"]
        )
        assert caps == KeyCapabilities(**expected)
        assert len(seen) == 1
        sent = seen[0]
        assert (sent.method, sent.url.path) == ("GET", AUTHORIZE_PATH)
        token = base64.b64encode(
            f"{request['key_id']}:{request['application_key']}".encode()
        ).decode()
        assert sent.headers["authorization"] == f"Basic {token}"
