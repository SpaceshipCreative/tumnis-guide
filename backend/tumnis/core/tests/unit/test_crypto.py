"""Envelope encryption: seal and open with the workspace data key (P0-08, SEC-6)."""

from __future__ import annotations

import uuid

import pytest
from hypothesis import given
from hypothesis import strategies as st

WORKSPACE = uuid.UUID("01890000-0000-7000-8000-000000000001")
OTHER_WORKSPACE = uuid.UUID("01890000-0000-7000-8000-000000000002")


@pytest.mark.req("SEC-6")
@pytest.mark.wp("P0-08")
@pytest.mark.xfail(strict=True, reason="spec:P0-08")
@given(
    plaintext=st.binary(max_size=2048),
    key=st.text(min_size=1, max_size=64),
    workspace_id=st.uuids(),
    key_version=st.integers(min_value=1, max_value=2**31 - 1),
)
def test_envelope_round_trip(
    plaintext: bytes, key: str, workspace_id: uuid.UUID, key_version: int
) -> None:
    """T-P0-08-07
    For any plaintext and setting key, open_sealed(seal(x)) == x with the same data key and
    AAD; the blob starts with the format byte and the big-endian key version.
    """
    from tumnis.core.crypto import (  # noqa: PLC0415
        FORMAT_V1,
        new_data_key,
        open_sealed,
        seal,
        setting_aad,
    )

    data_key = new_data_key()
    aad = setting_aad(workspace_id, key)
    blob = seal(data_key, key_version, plaintext, aad=aad)

    assert blob[:1] == FORMAT_V1
    assert int.from_bytes(blob[1:5], "big") == key_version
    assert open_sealed({key_version: data_key}, blob, aad=aad) == plaintext


@pytest.mark.req("SEC-6")
@pytest.mark.wp("P0-08")
@pytest.mark.xfail(strict=True, reason="spec:P0-08")
def test_tampered_or_moved_ciphertext_fails() -> None:
    """T-P0-08-08
    Flipping any byte of a sealed value, or opening it with another workspace's or another
    setting key's AAD, raises DecryptionError.
    """
    from tumnis.core.crypto import (  # noqa: PLC0415
        DecryptionError,
        new_data_key,
        open_sealed,
        seal,
        setting_aad,
    )

    data_key = new_data_key()
    keys = {1: data_key}
    aad = setting_aad(WORKSPACE, "decisions.jev")
    blob = seal(data_key, 1, b'{"api_key": "jev-secret"}', aad=aad)
    assert open_sealed(keys, blob, aad=aad) == b'{"api_key": "jev-secret"}'

    for index in range(len(blob)):
        tampered = bytearray(blob)
        tampered[index] ^= 0x01
        with pytest.raises(DecryptionError):
            open_sealed(keys, bytes(tampered), aad=aad)

    with pytest.raises(DecryptionError):
        open_sealed(keys, blob, aad=setting_aad(OTHER_WORKSPACE, "decisions.jev"))
    with pytest.raises(DecryptionError):
        open_sealed(keys, blob, aad=setting_aad(WORKSPACE, "calendar.google"))
    with pytest.raises(DecryptionError):
        open_sealed(keys, blob[:10], aad=aad)
