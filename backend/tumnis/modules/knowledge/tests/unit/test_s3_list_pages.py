"""An S3 listing page never comes back empty while more keys follow: a raw page made only
of keys Tumnis leaves out (`.tumnis/` objects, unsafe names) is skipped, so a caller that
stops on an empty page still sees every file (PR #52 review, FR-15.7)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from tumnis.modules.knowledge.adapters.s3 import S3Config, S3Storage

_WHEN = datetime(2026, 3, 9, 12, tzinfo=UTC)


class _ScriptedS3:
    """list_objects_v2 answering from scripted pages, keyed by continuation token."""

    def __init__(self, pages: dict[str | None, dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[dict[str, Any]] = []

    async def list_objects_v2(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        return self.pages[kw.get("ContinuationToken")]


def _obj(key: str) -> dict[str, Any]:
    return {"Key": key, "Size": 1, "LastModified": _WHEN, "ETag": '"e"'}


def _storage(client: _ScriptedS3) -> S3Storage:
    config = S3Config(
        endpoint="http://s3.example.com", region="us-east-1", access_key="a", secret_key="k"
    )
    storage = S3Storage(config, bucket="bucket", prefix="tumnis")

    async def scripted() -> _ScriptedS3:
        return client

    storage._s3 = scripted  # type: ignore[method-assign]
    return storage


@pytest.mark.req("FR-15.7")
@pytest.mark.wp("P1-14")
async def test_pr52_s3_list_skips_a_page_of_hidden_keys() -> None:
    client = _ScriptedS3(
        {
            None: {
                "Contents": [_obj("tumnis/.tumnis/health"), _obj("tumnis/a/../b")],
                "IsTruncated": True,
                "NextContinuationToken": "t1",
            },
            "t1": {"Contents": [_obj("tumnis/notes/plan.md")], "IsTruncated": False},
        }
    )
    page = await _storage(client).list("", None)
    assert [s.path for s in page.items] == ["notes/plan.md"]
    assert page.next_cursor is None
    assert [c.get("ContinuationToken") for c in client.calls] == [None, "t1"]
    assert {c["Prefix"] for c in client.calls} == {"tumnis/"}


@pytest.mark.req("FR-15.7")
@pytest.mark.wp("P1-14")
async def test_pr52_s3_list_ends_empty_when_only_hidden_keys_remain() -> None:
    client = _ScriptedS3(
        {
            None: {
                "Contents": [_obj("tumnis/.tumnis/probe-1")],
                "IsTruncated": True,
                "NextContinuationToken": "t1",
            },
            "t1": {"Contents": [_obj("tumnis/.tumnis/probe-2")], "IsTruncated": False},
        }
    )
    page = await _storage(client).list("", None)
    assert page.items == []
    assert page.next_cursor is None
