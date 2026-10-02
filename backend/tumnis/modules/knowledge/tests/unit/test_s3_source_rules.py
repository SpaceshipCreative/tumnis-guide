"""S3 buckets as a linked source (P3-13, FR-15.11): which keys Tumnis accepts, and how a
listed object compares with what the last sync saw."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest


def _rules() -> Any:
    """knowledge.rules, imported when a test runs."""
    return importlib.import_module("tumnis.modules.knowledge.rules")


T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
LATER = T0 + timedelta(minutes=5)
UNVERIFIED = "Tumnis could not verify this key is read-only"


def _caps(**over: Any) -> Any:
    KeyCapabilities = _rules().KeyCapabilities  # noqa: N806

    base: dict[str, Any] = {
        "checked": True,
        "can_read": True,
        "can_list": True,
        "can_write": False,
        "can_delete": False,
        "bucket_scoped": True,
        "prefix": None,
        "source": "b2_authorize_account",
    }
    return KeyCapabilities(**(base | over))


# name -> (capabilities overrides, accepted, reason or warning)
CAPABILITY_TABLE: dict[str, tuple[dict[str, Any], bool, str | None]] = {
    "read_only_scoped": ({}, True, None),
    "read_only_scoped_to_prefix": ({"prefix": "acme/"}, True, None),
    "minio_read_only": ({"source": "minio_account_info"}, True, None),
    "can_write": ({"can_write": True}, False, "key_can_write"),
    "can_delete": ({"can_delete": True}, False, "key_can_delete"),
    "write_and_delete": ({"can_write": True, "can_delete": True}, False, "key_can_write"),
    "not_bucket_scoped": ({"bucket_scoped": False}, False, "key_not_scoped"),
    "unchecked": (
        {
            "checked": False,
            "can_read": None,
            "can_list": None,
            "can_write": None,
            "can_delete": None,
            "bucket_scoped": None,
            "source": "none",
        },
        True,
        UNVERIFIED,
    ),
}


@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
@pytest.mark.xfail(strict=True, reason="spec:P3-13")
@pytest.mark.parametrize("case", list(CAPABILITY_TABLE))
def test_capabilities_table(case: str) -> None:
    """T-P3-13-01
    A checked key that can write, can delete or is not limited to the bucket is refused
    (the reason names which, write first); a checked read-only key scoped to the bucket is
    accepted with no warning; a key the provider gives no way to check (AWS and others) is
    accepted with the visible "could not verify" warning.
    """
    capabilities_acceptable = _rules().capabilities_acceptable

    over, accepted, message = CAPABILITY_TABLE[case]
    assert capabilities_acceptable(_caps(**over)) == (accepted, message)


def _prev(etag: str, size: int, mtime: datetime) -> Any:
    FolderFileLite = _rules().FolderFileLite  # noqa: N806

    return FolderFileLite(etag=etag, size=size, mtime=mtime)


def _obj(etag: str, size: int, last_modified: datetime) -> Any:
    S3ObjectLite = _rules().S3ObjectLite  # noqa: N806

    return S3ObjectLite(key="acme/brief.pdf", etag=etag, size=size, last_modified=last_modified)


SINGLE = "9b2cf535f27731c974343645a3985328"
OTHER = "0cc175b9c0f1b6a831c399e269772661"
MULTIPART = "d41d8cd98f00b204e9800998ecf8427e-3"

# name -> (previous record or None, listed object or None, expected)
CHANGE_TABLE: dict[str, tuple[tuple[Any, ...] | None, tuple[Any, ...] | None, str]] = {
    "new": (None, (SINGLE, 10, T0), "new"),
    "unchanged": ((SINGLE, 10, T0), (SINGLE, 10, T0), "unchanged"),
    "changed_by_etag": ((SINGLE, 10, T0), (OTHER, 10, LATER), "changed"),
    "changed_by_etag_same_time": ((SINGLE, 10, T0), (OTHER, 10, T0), "changed"),
    "etag_case_and_quotes_ignored": (
        (SINGLE, 10, T0),
        (f'"{SINGLE.upper()}"', 10, T0),
        "unchanged",
    ),
    "deleted": ((SINGLE, 10, T0), None, "deleted"),
    # A multipart ETag is not a content hash: the same ETag with a newer time and a new
    # size is a different object.
    "multipart_same_etag_newer_other_size": (
        (MULTIPART, 10, T0),
        (MULTIPART, 12, LATER),
        "changed",
    ),
    "multipart_same_etag_newer_same_size": (
        (MULTIPART, 10, T0),
        (MULTIPART, 10, LATER),
        "unchanged",
    ),
    "same_etag_older_other_size": ((MULTIPART, 10, LATER), (MULTIPART, 12, T0), "unchanged"),
}


@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
@pytest.mark.xfail(strict=True, reason="spec:P3-13")
@pytest.mark.parametrize("case", list(CHANGE_TABLE))
def test_change_detection_table(case: str) -> None:
    """T-P3-13-04
    A key the last sync did not see is new; one it saw that is gone is deleted; a differing
    ETag (quotes and case ignored) is a change; an equal ETag is unchanged unless the object
    is newer and its size differs, the multipart case where the ETag is not a content hash.
    """
    s3_change = _rules().s3_change

    prev, obj, expected = CHANGE_TABLE[case]
    assert s3_change(_prev(*prev) if prev else None, _obj(*obj) if obj else None) == expected
