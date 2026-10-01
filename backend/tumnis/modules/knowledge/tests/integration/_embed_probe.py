"""Imported by the worker subprocesses of T-P3-10-08 (`worker_killer(imports=...)`): the
Embeddings slot answers with two fakes, model A (`model-a`, 4 dimensions, local) and
model B (`model-b`, 6 dimensions, local), B preferred, and every text sent to B is
appended to the file `KNOWLEDGE_EMBED_LOG` names, one line each, so the parent process can
count them across the killed worker and its replacement."""

import os
from collections.abc import Sequence
from pathlib import Path

from tumnis.modules.decisions import api as decisions
from tumnis.modules.decisions.adapters.embeddings.fake import (
    FakeEmbeddings,
)

MODEL_A = "model-a"
MODEL_B = "model-b"


def _append(path: Path, lines: list[str]) -> None:
    with path.open("a") as f:
        f.writelines(line + "\n" for line in lines)


class _LoggedFake(FakeEmbeddings):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = await super().embed(texts)
        log = os.environ.get("KNOWLEDGE_EMBED_LOG")
        if log:
            _append(Path(log), [text.replace("\n", " ") for text in texts])
        return vectors


decisions.use_embedders(
    decisions.Embedders(
        (_LoggedFake(model=MODEL_B, dims=6), FakeEmbeddings(model=MODEL_A, dims=4)),
        primary=MODEL_B,
    )
)
