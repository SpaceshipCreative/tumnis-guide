"""Folder backups copy and never sync (P0-28, REL-1): rclone against the MinIO fixture."""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import aioboto3  # type: ignore[import-untyped]
import pytest

if TYPE_CHECKING:
    from tests._services import S3Endpoint

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

REPO = Path(__file__).resolve().parents[5]
# rclone runs in its own container, so the test needs Docker (as the fixtures do), not a
# local rclone install. Pinned by digest (multi-arch index of rclone/rclone:1.75.1).
RCLONE_IMAGE = (
    "rclone/rclone:1.75.1@sha256:45401ad7410db1d67ffdb58e19059ad20b0d8e0285a60e38bbec55cc1019c7a5"
)


def _endpoint_from_container(url: str) -> str:
    """The MinIO URL as a container sees it: the host's published port through the host
    gateway (the fixture hands out localhost)."""
    parts = urlsplit(url)
    host = parts.hostname or "localhost"
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = "host.docker.internal"
    return f"{parts.scheme}://{host}:{parts.port}"


async def _make_bucket(minio: S3Endpoint, bucket: str) -> None:
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=minio.url,
        aws_access_key_id=minio.access_key,
        aws_secret_access_key=minio.secret_key,
        region_name=minio.region,
    ) as s3:
        await s3.create_bucket(Bucket=bucket)


async def _keys(minio: S3Endpoint, bucket: str, prefix: str) -> list[str]:
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=minio.url,
        aws_access_key_id=minio.access_key,
        aws_secret_access_key=minio.secret_key,
        region_name=minio.region,
    ) as s3:
        listing = await s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    return sorted(item["Key"] for item in listing.get("Contents", []))


@pytest.mark.req("REL-1")
@pytest.mark.wp("P0-28")
async def test_rclone_copy_keeps_files_removed_at_source(minio: S3Endpoint) -> None:
    """T-P0-28-04
    rclone configured through RCLONE_CONFIG_MINIO_* only. scripts/drill/rclone_copy_check.sh
    writes a.txt in a temporary source, copies it with the folder backup script, deletes
    a.txt at the source, copies again, and requires `rclone lsf` to still list a.txt. The
    object is also still in the bucket when the test lists it itself.
    """
    docker = shutil.which("docker")
    assert docker, "the rclone check runs rclone in a container"
    bucket = f"rclone-{uuid.uuid4().hex[:8]}"
    await _make_bucket(minio, bucket)

    env = {
        "RCLONE_CONFIG_MINIO_TYPE": "s3",
        "RCLONE_CONFIG_MINIO_PROVIDER": "Minio",
        "RCLONE_CONFIG_MINIO_ENDPOINT": _endpoint_from_container(minio.url),
        "RCLONE_CONFIG_MINIO_ACCESS_KEY_ID": minio.access_key,
        "RCLONE_CONFIG_MINIO_SECRET_ACCESS_KEY": minio.secret_key,
        "RCLONE_CONFIG_MINIO_REGION": minio.region,
    }
    command = [
        docker,
        "run",
        "--rm",
        "--add-host=host.docker.internal:host-gateway",
        *(arg for name in env for arg in ("-e", name)),
        "-v",
        f"{REPO}:/repo:ro",
        "--entrypoint",
        "/bin/sh",
        RCLONE_IMAGE,
        "/repo/scripts/drill/rclone_copy_check.sh",
        f"minio:{bucket}/t",
    ]
    process = await asyncio.create_subprocess_exec(
        *command,
        env={**os.environ, **env},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await asyncio.wait_for(process.communicate(), timeout=180)
    assert process.returncode == 0, output.decode()
    assert "t/a.txt" in await _keys(minio, bucket, "t/")
