"""Envelope encryption: the master key file, per-workspace data keys, AES-256-GCM seal and
open, and the master key re-wrap (P0-08, SEC-6, ADR-0010)."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

FORMAT_V1 = b"\x01"


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
    raise NotImplementedError


def wrap(
    master: MasterKeys, data_key: bytes, *, workspace_id: UUID, key_version: int
) -> tuple[int, bytes]:
    raise NotImplementedError


def unwrap(
    master: MasterKeys, wrapped: bytes, *, master_version: int, workspace_id: UUID, key_version: int
) -> bytes:
    raise NotImplementedError


def seal(data_key: bytes, key_version: int, plaintext: bytes, *, aad: bytes) -> bytes:
    raise NotImplementedError


def open_sealed(keys_by_version: Mapping[int, bytes], blob: bytes, *, aad: bytes) -> bytes:
    raise NotImplementedError


def setting_aad(workspace_id: UUID, key: str) -> bytes:
    raise NotImplementedError


async def rewrap_all(session: AsyncSession, master: MasterKeys, *, to_version: int) -> int:
    raise NotImplementedError
