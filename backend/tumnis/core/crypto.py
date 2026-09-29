"""Envelope encryption: the master key file, per-workspace data keys, AES-256-GCM seal and
open, and the master key re-wrap (P0-08, SEC-6, ADR-0010).

Each workspace has a random 256-bit data key (`workspace_keys`), stored wrapped by the
deployment master key; settings are sealed with the data key. The master key file is
JSON, {"active": 2, "keys": {"1": "<base64>", "2": "<base64>"}}, readable by its owner
only. Rotation adds a version, points `active` at it, re-wraps every data key
(`tumnis keys rotate-master --to <v>`) and then drops the old version: the sealed values
never change.

Never log a DecryptionError's inputs; the messages here never carry them.
"""

import base64
import binascii
import hashlib
import json
import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
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
        """sha256 hex of the key, first 16 characters (safe to log and to compare)."""
        return hashlib.sha256(self.keys[version]).hexdigest()[:16]


GROUP_OR_OTHERS = 0o077


def load_master_keys(path: str, *, strict_owner: bool) -> MasterKeys:
    """Reads the key file. Refuses (MasterKeyError) a file that is missing, not a regular
    file, readable (or writable) by group or others, malformed, or, with `strict_owner`
    (prod), owned by anyone but root or the process user."""
    try:
        info = os.stat(path)
    except FileNotFoundError:
        raise MasterKeyError(f"master key file not found: {path}") from None
    except OSError as exc:
        raise MasterKeyError(f"master key file unreadable: {path}: {exc.strerror}") from None
    if not stat.S_ISREG(info.st_mode):
        raise MasterKeyError("master key file must be a regular file")
    if info.st_mode & GROUP_OR_OTHERS:
        raise MasterKeyError("master key file must not be readable by group or others")
    if strict_owner and info.st_uid not in (0, os.getuid()):
        raise MasterKeyError("master key file must be owned by root or the service user")
    try:
        body = json.loads(Path(path).read_bytes())
        active = int(body["active"])
        keys = {
            int(version): base64.b64decode(value, validate=True)
            for version, value in body["keys"].items()
        }
    except (OSError, ValueError, TypeError, KeyError, AttributeError, binascii.Error):
        raise MasterKeyError(
            'master key file must be JSON {"active": <n>, "keys": {"<n>": "<base64>"}}'
        ) from None
    if active not in keys:
        raise MasterKeyError(f"master key file names active version {active} but lacks it")
    if any(len(key) != KEY_BYTES for key in keys.values()):
        raise MasterKeyError(f"every master key must be {KEY_BYTES} bytes")
    return MasterKeys(active=active, keys=keys)


@dataclass
class _State:
    loader: Callable[[], MasterKeys] | None = None
    keys: MasterKeys | None = None
    # Unwrapped data keys by (workspace, key version): this process's memory only.
    data_keys: dict[tuple[UUID, int], bytes] = field(default_factory=dict)


_state = _State()


def configure_master_keys(loader: Callable[[], MasterKeys]) -> None:
    """Where this process gets its master keys (create_app, the worker, tests). Forgets the
    keys loaded before and every data key they unwrapped."""
    _state.loader, _state.keys = loader, None
    _state.data_keys.clear()


def reset_master_keys() -> None:
    configure_master_keys(_unconfigured)


def _unconfigured() -> MasterKeys:
    raise MasterKeyError("no master key file is configured in this process")


def master_keys() -> MasterKeys:
    """The configured master keys, loaded once."""
    if _state.keys is None:
        _state.keys = (_state.loader or _unconfigured)()
    return _state.keys


def remembered_data_key(workspace_id: UUID, key_version: int) -> bytes | None:
    return _state.data_keys.get((workspace_id, key_version))


def remember_data_key(workspace_id: UUID, key_version: int, data_key: bytes) -> None:
    _state.data_keys[(workspace_id, key_version)] = data_key


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
