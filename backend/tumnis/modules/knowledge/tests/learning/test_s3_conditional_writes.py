"""Learning tests: how S3-compatible providers treat conditional puts (P1-14, FR-15.7).

They answer architecture open question 6 with a committed record: MinIO runs on every PR
against the pinned image; a real B2 bucket is probed by hand and its row recorded.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from tumnis.modules.knowledge.tests.contract.test_storage_s3 import make_bucket, s3_storage

if TYPE_CHECKING:
    from tests._services import S3Endpoint

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

RECORD = Path(__file__).with_name("s3_conditional_writes.yaml")
B2_ENV = ("TUMNIS_B2_ENDPOINT", "TUMNIS_B2_BUCKET", "TUMNIS_B2_KEY_ID", "TUMNIS_B2_KEY_FILE")


def _read_key(path: str) -> str:
    return Path(path).read_text().strip()


def _record() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(RECORD.read_text())
    return data


@pytest.mark.req("FR-15.7")
@pytest.mark.wp("P1-14")
@pytest.mark.xfail(strict=True, reason="spec:P1-14")
async def test_minio_behavior_matches_record(minio: S3Endpoint) -> None:
    """T-P1-14-10
    Against the pinned MinIO image, `IfNoneMatch='*'` on an existing key and a stale
    `IfMatch` behave as recorded in `s3_conditional_writes.yaml`, and the probe S3Storage
    runs at location save gives the same answers.
    """
    from tests._services import MINIO_IMAGE  # noqa: PLC0415

    record = _record()
    assert record["image"] == MINIO_IMAGE
    backend = s3_storage(minio, await make_bucket(minio))
    try:
        probe = await backend.probe_conditional_writes()
    finally:
        await backend.aclose()
    assert probe.if_none_match_star == record["if_none_match_star"]
    assert probe.stale_if_match == record["stale_if_match"]
    assert probe.conditional_put == (
        record["if_none_match_star"] == record["stale_if_match"] == "precondition_failed"
    )


@pytest.mark.req("FR-15.7")
@pytest.mark.wp("P1-14")
@pytest.mark.skipif(
    not all(os.environ.get(name) for name in B2_ENV),
    reason="manual: set TUMNIS_B2_ENDPOINT, TUMNIS_B2_BUCKET, TUMNIS_B2_KEY_ID and "
    "TUMNIS_B2_KEY_FILE (a file holding the application key) to probe a real B2 bucket",
)
async def test_b2_behavior_recorded() -> None:
    """Manual learning test: probes a real B2 bucket (an empty scratch bucket; the probe
    writes and deletes two objects under `.tumnis/`) and compares the answers with the
    `providers.b2` row of `s3_conditional_writes.yaml`. To record the row, run it once with
    the row null: the failure message prints the answers to paste in.
    """
    from tumnis.modules.knowledge.adapters.s3 import S3Config, S3Storage  # noqa: PLC0415

    config = S3Config(
        endpoint=os.environ["TUMNIS_B2_ENDPOINT"],
        region=os.environ.get("TUMNIS_B2_REGION", "us-west-004"),
        access_key=os.environ["TUMNIS_B2_KEY_ID"],
        secret_key=_read_key(os.environ["TUMNIS_B2_KEY_FILE"]),
        path_style=False,
    )
    backend = S3Storage(config, bucket=os.environ["TUMNIS_B2_BUCKET"])
    try:
        probe = await backend.probe_conditional_writes()
    finally:
        await backend.aclose()
    answers = {
        "if_none_match_star": probe.if_none_match_star,
        "stale_if_match": probe.stale_if_match,
    }
    assert _record()["providers"]["b2"] == answers, f"record providers.b2 as {answers}"
