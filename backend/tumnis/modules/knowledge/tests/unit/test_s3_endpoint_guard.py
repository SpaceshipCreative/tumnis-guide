"""aioboto3 brings its own HTTP stack instead of `tumnis.core.net` (allowed by Scott on
PR #52, decision 6), so the S3 adapter checks its endpoint against the same NetPolicy as
`guarded_client` before it opens a client: a metadata or loopback endpoint is refused and
nothing is sent (unit tests have no sockets, so a send would fail differently)."""

from __future__ import annotations

import pytest

from tumnis.core.net import NetPolicy, ScriptedResolver, SsrfBlocked
from tumnis.modules.knowledge.adapters.s3 import S3Config, S3Storage


@pytest.mark.req("SEC-5")
@pytest.mark.wp("P1-14")
@pytest.mark.parametrize(
    ("mode", "endpoint", "answer"),
    [
        ("self-hosted", "http://169.254.169.254", "169.254.169.254"),
        ("self-hosted", "http://metadata.example.com", "169.254.169.254"),
        ("self-hosted", "http://minio.example.com:9000", "127.0.0.1"),
        ("hosted", "https://s3.example.com", "169.254.169.254"),
        ("hosted", "https://s3.example.com", "10.0.0.5"),
    ],
)
async def test_pr52_s3_adapter_refuses_blocked_endpoint_before_sending(
    mode: str, endpoint: str, answer: str
) -> None:
    resolver = ScriptedResolver([[answer]])
    storage = S3Storage(
        S3Config(endpoint=endpoint, region="us-east-1", access_key="AKIA", secret_key="k"),
        bucket="bucket",
        prefix="tumnis",
        net_policy=NetPolicy(mode=mode),  # type: ignore[arg-type]
        resolver=resolver,
    )
    try:
        with pytest.raises(SsrfBlocked):
            await storage.stat("notes/plan.md")
    finally:
        await storage.aclose()
    assert len(resolver.calls) <= 1  # the endpoint check only; no connection resolved
