"""Envelope encryption: the master key file, per-workspace data keys, AES-256-GCM seal and
open, and the master key re-wrap (P0-08, SEC-6, ADR-0010)."""

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.ext.asyncio import AsyncSession

FORMAT_V1 = b"\x01"
KEY_BYTES = 32  # AES-256
NONCE_BYTES = 12  # 96-bit random nonces (AES-GCM)
_HEADER = 1 + 4  # format byte + big-endian key version


class MasterKeyError(RuntimeError):
    """The master key file is missing, unsafe or malformed."""


class DecryptionError(ValueError):
    """A sealed value or a wrapped key failed to authenticate."""


@dataclass(frozen=True)
class MasterKeys:
    active: int
    keys: Mapping[int, bytes]

    def fingerprint(self, version: int) -> str:
        raise NotImplementedError


def load_master_keys(path: str, *, strict_owner: bool) -> MasterKeys:
    raise NotImplementedError


def configure_master_keys(loader: Callable[[], MasterKeys]) -> None:
    raise NotImplementedError


def reset_master_keys() -> None:
    raise NotImplementedError


def master_keys() -> MasterKeys:
    raise NotImplementedError


def new_data_key() -> bytes:
    return os.urandom(KEY_BYTES)


def wrap(
    master: MasterKeys, data_key: bytes, *, workspace_id: UUID, key_version: int
) -> tuple[int, bytes]:
    raise NotImplementedError


def unwrap(
    master: MasterKeys, wrapped: bytes, *, master_version: int, workspace_id: UUID, key_version: int
) -> bytes:
    raise NotImplementedError


def seal(data_key: bytes, key_version: int, plaintext: bytes, *, aad: bytes) -> bytes:
    """FORMAT_V1 + key version (4 bytes, big-endian) + nonce + AES-GCM ciphertext and tag."""
    nonce = os.urandom(NONCE_BYTES)
    header = FORMAT_V1 + key_version.to_bytes(4, "big")
    return header + nonce + AESGCM(data_key).encrypt(nonce, plaintext, aad)


def sealed_key_version(blob: bytes) -> int:
    """The data key version a sealed value names in its header."""
    if len(blob) < _HEADER or blob[:1] != FORMAT_V1:
        raise DecryptionError("not a sealed value")
    return int.from_bytes(blob[1:_HEADER], "big")


def open_sealed(keys_by_version: Mapping[int, bytes], blob: bytes, *, aad: bytes) -> bytes:
    """Reads the version header, picks the data key and decrypts. Any mismatch (format,
    unknown version, tampering, another workspace's or setting's AAD) is DecryptionError,
    whose message never carries the inputs."""
    data_key = keys_by_version.get(sealed_key_version(blob))
    nonce = blob[_HEADER : _HEADER + NONCE_BYTES]
    if data_key is None or len(nonce) != NONCE_BYTES:
        raise DecryptionError("no data key for this sealed value")
    try:
        return AESGCM(data_key).decrypt(nonce, blob[_HEADER + NONCE_BYTES :], aad)
    except InvalidTag:
        raise DecryptionError("sealed value failed to authenticate") from None


def setting_aad(workspace_id: UUID, key: str) -> bytes:
    """Binds a sealed setting to its workspace and key: a value copied elsewhere fails."""
    return f"tumnis:setting:v1:{workspace_id}:{key}".encode()


async def rewrap_all(session: AsyncSession, master: MasterKeys, *, to_version: int) -> int:
    raise NotImplementedError
