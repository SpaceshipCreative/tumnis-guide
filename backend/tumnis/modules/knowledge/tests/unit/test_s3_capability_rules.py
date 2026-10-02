"""The pure parts of the S3 linked source's key check and mapping (P3-13, FR-15.11):
B2's allowed capabilities, a MinIO account policy, the master-key heuristic and how keys
fall under mapped prefixes."""

from __future__ import annotations

from typing import Any

import pytest

from tumnis.modules.knowledge.rules import (
    b2_key_capabilities,
    capabilities_acceptable,
    looks_like_b2_master_key_id,
    minio_key_capabilities,
    normalize_prefix,
    prefix_for,
    s3_change,
)

BUCKET = "tumnis-docs"


def _statement(actions: list[str], resources: list[str], **extra: Any) -> dict[str, Any]:
    return {"Effect": "Allow", "Action": actions, "Resource": resources, **extra}


READ_ONLY = {
    "Version": "2012-10-17",
    "Statement": [
        _statement(["s3:GetObject"], [f"arn:aws:s3:::{BUCKET}/acme/*"]),
        _statement(
            ["s3:ListBucket"],
            [f"arn:aws:s3:::{BUCKET}"],
            Condition={"StringLike": {"s3:prefix": ["acme/*"]}},
        ),
    ],
}


@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
def test_minio_read_only_policy_is_read_only_and_scoped() -> None:
    caps = minio_key_capabilities(READ_ONLY, BUCKET, ["acme/"])
    assert (caps.checked, caps.source) == (True, "minio_account_info")
    assert (caps.can_read, caps.can_list, caps.can_write, caps.can_delete) == (
        True,
        True,
        False,
        False,
    )
    assert caps.bucket_scoped is True
    assert capabilities_acceptable(caps) == (True, None)


@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        (_statement(["s3:PutObject"], [f"arn:aws:s3:::{BUCKET}/*"]), "key_can_write"),
        (_statement(["s3:Put*"], [f"arn:aws:s3:::{BUCKET}/acme/*"]), "key_can_write"),
        (_statement(["s3:*"], [f"arn:aws:s3:::{BUCKET}/*"]), "key_can_write"),
        (_statement(["s3:DeleteObject"], [f"arn:aws:s3:::{BUCKET}/*"]), "key_can_delete"),
        (_statement(["admin:*"], ["arn:minio:admin:::*"]), "key_can_write"),
        (_statement(["s3:GetObject"], ["arn:aws:s3:::*"]), "key_not_scoped"),
        (_statement(["s3:GetObject"], ["*"]), "key_not_scoped"),
        ({"Effect": "Allow", "NotAction": ["s3:GetObject"], "Resource": ["*"]}, "key_can_write"),
        (
            {"Effect": "Allow", "Action": ["s3:GetObject"], "NotResource": ["arn:aws:s3:::x/*"]},
            "key_not_scoped",
        ),
    ],
)
def test_minio_policy_refusals(statement: dict[str, Any], expected: str) -> None:
    policy = {"Version": "2012-10-17", "Statement": [*READ_ONLY["Statement"], statement]}
    caps = minio_key_capabilities(policy, BUCKET, ["acme/"])
    assert capabilities_acceptable(caps) == (False, expected)


@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
@pytest.mark.parametrize(
    ("actions", "expected"),
    [
        # Bucket-level writes change or empty the bucket without touching an object
        # (#158 review): a lifecycle rule can expire every object.
        (["s3:PutLifecycleConfiguration"], (False, "key_can_write")),
        (["s3:PutBucketPolicy"], (False, "key_can_write")),
        (["s3:PutBucketVersioning"], (False, "key_can_write")),
        (["s3:DeleteBucket"], (False, "key_can_delete")),
        (["s3:Delete*"], (False, "key_can_delete")),
        (["s3:*"], (False, "key_can_write")),
        (["*"], (False, "key_can_write")),
        (["s3:?ut*"], (False, "key_can_write")),
        # Reads on the bucket stay acceptable.
        (["s3:Get*", "s3:List*"], (True, None)),
        (["s3:GetBucketLocation", "s3:ListBucketVersions"], (True, None)),
    ],
)
def test_minio_bucket_level_actions(actions: list[str], expected: tuple[bool, str | None]) -> None:
    bucket_wide = _statement(actions, [f"arn:aws:s3:::{BUCKET}"])
    policy = {"Statement": [*READ_ONLY["Statement"], bucket_wide]}
    assert capabilities_acceptable(minio_key_capabilities(policy, BUCKET, ["acme/"])) == expected


@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
def test_minio_put_on_another_prefix_does_not_count() -> None:
    other = _statement(["s3:PutObject"], [f"arn:aws:s3:::{BUCKET}/uploads/*"])
    policy = {"Statement": [*READ_ONLY["Statement"], other]}
    assert capabilities_acceptable(minio_key_capabilities(policy, BUCKET, ["acme/"])) == (
        True,
        None,
    )
    # ... but it does once that prefix is mapped too.
    caps = minio_key_capabilities(policy, BUCKET, ["acme/", "uploads/"])
    assert caps.can_write is True


@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
@pytest.mark.parametrize(
    ("actions", "resource", "expected"),
    [
        # A grant narrower than a mapped prefix still reaches synced objects (#158 review).
        (["s3:DeleteObject"], f"arn:aws:s3:::{BUCKET}/acme/sub/*", (False, "key_can_delete")),
        (["s3:PutObject"], f"arn:aws:s3:::{BUCKET}/acme/sub/*", (False, "key_can_write")),
        (["s3:PutObject"], f"arn:aws:s3:::{BUCKET}/acme/report.md", (False, "key_can_write")),
        (["s3:DeleteObjectVersion"], f"arn:aws:s3:::{BUCKET}/acme/a?/*", (False, "key_can_delete")),
        (["s3:PutObject"], f"arn:aws:s3:::{BUCKET}/acme/reports/*", (False, "key_can_write")),
        # ... and so does one wider than it that a wildcard reaches into.
        (["s3:PutObject"], f"arn:aws:s3:::{BUCKET}/ac*", (False, "key_can_write")),
        (["s3:DeleteObject"], f"arn:aws:s3:::{BUCKET}/a*/x", (False, "key_can_delete")),
        # `?` takes exactly one character: `acme?` names no object under `acme/`.
        (["s3:PutObject"], f"arn:aws:s3:::{BUCKET}/acme?", (True, None)),
        # A sibling prefix that only shares a start with the mapped one does not.
        (["s3:PutObject"], f"arn:aws:s3:::{BUCKET}/acme-old/*", (True, None)),
        (["s3:DeleteObject"], f"arn:aws:s3:::{BUCKET}/uploads/acme/*", (True, None)),
    ],
)
def test_minio_grants_overlapping_a_mapped_prefix_count(
    actions: list[str], resource: str, expected: tuple[bool, str | None]
) -> None:
    policy = {"Statement": [*READ_ONLY["Statement"], _statement(actions, [resource])]}
    assert capabilities_acceptable(minio_key_capabilities(policy, BUCKET, ["acme/"])) == expected


@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
def test_minio_empty_or_odd_policy_is_not_scoped() -> None:
    for policy in (None, {}, {"Statement": "nope"}, {"Statement": [{"Effect": "Deny"}]}):
        caps = minio_key_capabilities(policy, BUCKET, [])
        assert capabilities_acceptable(caps) == (False, "key_not_scoped")


@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
def test_b2_capabilities_edges() -> None:
    other_bucket = {"buckets": [{"id": "b", "name": "another"}], "capabilities": ["readFiles"]}
    assert b2_key_capabilities(other_bucket, BUCKET).bucket_scoped is False
    two = {
        "buckets": [{"id": "a", "name": BUCKET}, {"id": "b", "name": "another"}],
        "capabilities": ["readFiles"],
    }
    assert b2_key_capabilities(two, BUCKET).bucket_scoped is False
    retention = {
        "buckets": [{"id": "a", "name": BUCKET}],
        "capabilities": ["readFiles", "writeFileRetentions"],
    }
    assert capabilities_acceptable(b2_key_capabilities(retention, BUCKET)) == (
        False,
        "key_can_write",
    )
    odd = {"buckets": "x", "capabilities": "readFiles", "namePrefix": ""}
    caps = b2_key_capabilities(odd, BUCKET)
    assert (caps.can_read, caps.bucket_scoped, caps.prefix) == (False, False, None)


@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
def test_master_key_heuristic() -> None:
    assert looks_like_b2_master_key_id("0123456789ab")
    assert not looks_like_b2_master_key_id("test-key-id")
    assert not looks_like_b2_master_key_id("0123456789ab0000000000001")


@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
def test_prefix_mapping() -> None:
    assert [normalize_prefix(p) for p in ("acme", "/acme/", "", " lab/x ")] == [
        "acme/",
        "acme/",
        "",
        "lab/x/",
    ]
    prefixes = ["acme/", "acme/old/", ""]
    assert prefix_for("acme/brief.md", prefixes) == "acme/"
    assert prefix_for("acme/old/a.md", prefixes) == "acme/old/"
    assert prefix_for("other/a.md", prefixes) == ""
    assert prefix_for("acme/", prefixes) is None
    assert prefix_for("acme-old/a.md", ["acme/"]) is None


@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
def test_change_needs_one_side() -> None:
    with pytest.raises(ValueError, match="needs"):
        s3_change(None, None)
