"""Fractional ranking keys (P0-17, FR-2.1): interface stub; the port lands with the
green step."""

from typing import Final

MAX_KEY_LEN: Final = 48


class RankError(ValueError):
    """An invalid key, or a >= b."""


def validate(key: str) -> None:
    raise NotImplementedError


def between(a: str | None, b: str | None) -> str:
    raise NotImplementedError


def n_keys(n: int) -> list[str]:
    raise NotImplementedError
