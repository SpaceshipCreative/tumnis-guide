"""An S3 location saved before the https rule, or edited in the database, still never
talks plain http in hosted mode: the adapter itself refuses (PR #52 review, SEC-5)."""

from __future__ import annotations

import pytest

from tumnis.core.adapters.base import AdapterRejected
from tumnis.core.net import NetPolicy, ScriptedResolver
from tumnis.modules.knowledge.adapters.s3 import S3Config, S3Storage


def _storage(endpoint: str, mode: str) -> S3Storage:
    config = S3Config(endpoint=endpoint, region="us-east-1", access_key="AKIA", secret_key="k")
    return S3Storage(
        config,
        bucket="bucket",
        prefix="tumnis",
        net_policy=NetPolicy(mode=mode),  # type: ignore[arg-type]
        resolver=ScriptedResolver([["93.184.216.34"]]),
    )


@pytest.mark.req("SEC-5")
@pytest.mark.wp("P1-14")
async def test_pr52_s3_adapter_refuses_plain_http_in_hosted_mode() -> None:
    storage = _storage("http://s3.example.com", "hosted")
    try:
        with pytest.raises(AdapterRejected):
            await storage.stat("notes/plan.md")
    finally:
        await storage.aclose()
