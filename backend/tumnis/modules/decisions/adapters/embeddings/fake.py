"""`FakeEmbeddings`: the Embeddings slot's fake (P3-10). Implements the port directly.

- Default: deterministic unit vectors derived from SHA-256 of the text (the same text
  always gets the same vector; different texts are near-orthogonal).
- `recorded=<dir>`: vectors looked up by SHA-256 of the exact text and the model in
  `<dir>/chunks.jsonl` and `<dir>/queries.jsonl` (lines `{"text_sha256", "model",
  "vector"}`); a text without a recording raises LookupError, so a changed chunker shows
  up as a failing test rather than a silent drop.
- `calls`: one entry per non-empty `embed` call, the texts sent. `hold`: an event the
  fake waits for before answering (tests use it to keep a re-embed in flight).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from typing import TYPE_CHECKING

from tumnis.core.adapters.registry import Health

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

__all__ = ["DEFAULT_DIMS", "DEFAULT_MODEL", "FakeEmbeddings", "FakeHostedEmbeddings", "text_sha256"]

DEFAULT_MODEL = "BAAI/bge-m3"  # the slot's default model (knowledge.rules)
DEFAULT_DIMS = 1024
RECORDED_FILES = ("chunks.jsonl", "queries.jsonl")
HOLD_POLL_S = 0.01


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _hashed_vector(text: str, dims: int) -> list[float]:
    raw: list[float] = []
    counter = 0
    while len(raw) < dims:
        digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
        raw += [(b - 127.5) / 127.5 for b in digest]
        counter += 1
    raw = raw[:dims]
    norm = math.sqrt(sum(x * x for x in raw)) or 1.0
    return [x / norm for x in raw]


def _load(directory: Path, model: str) -> dict[str, list[float]]:
    found: dict[str, list[float]] = {}
    for name in RECORDED_FILES:
        path = directory / name
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row["model"] == model:
                found[row["text_sha256"]] = [float(x) for x in row["vector"]]
    return found


class FakeEmbeddings:
    model: str = DEFAULT_MODEL
    dims: int = DEFAULT_DIMS
    hosted: bool = False

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        dims: int = DEFAULT_DIMS,
        *,
        hosted: bool = False,
        recorded: Path | None = None,
    ) -> None:
        self.model = model
        self.dims = dims
        self.hosted = hosted
        self.calls: list[list[str]] = []
        self.hold: asyncio.Event | None = None
        self._recorded = None if recorded is None else _load(recorded, model)

    def _vector(self, text: str) -> list[float]:
        if self._recorded is None:
            return _hashed_vector(text, self.dims)
        key = text_sha256(text)
        vector = self._recorded.get(key)
        if vector is None:
            raise LookupError(f"no recorded {self.model} vector for text sha256 {key}")
        return vector

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        # Polled, not awaited: a queued workflow runs on DBOS's own event loop, and an
        # Event set from the test's loop never wakes a waiter on another loop.
        while self.hold is not None and not self.hold.is_set():  # noqa: ASYNC110
            await asyncio.sleep(HOLD_POLL_S)
        self.calls.append(list(texts))
        return [self._vector(text) for text in texts]

    async def health(self) -> Health:
        return "ok"


class FakeHostedEmbeddings(FakeEmbeddings):
    """The hosted embedder's fake: `hosted=True` unless told otherwise."""

    hosted: bool = True

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        dims: int = DEFAULT_DIMS,
        *,
        hosted: bool = True,
        recorded: Path | None = None,
    ) -> None:
        super().__init__(model, dims, hosted=hosted, recorded=recorded)
