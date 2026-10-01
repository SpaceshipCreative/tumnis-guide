"""Record the search eval set's vectors (P3-10, FR-15.3): one-off, run by hand.

Reads `backend/fixtures/search_eval/{corpus,queries}.jsonl` and writes
`vectors/chunks.jsonl` (one line per chunk, embedded as its `context_text`: the heading
path and the text joined by newlines, as knowledge indexes it) and `vectors/queries.jsonl`
(one line per query text), each line `{"text_sha256", "model", "vector"}` with floats
rounded to 6 decimals. `FakeEmbeddings(recorded=...)` looks them up by SHA-256 of the
exact text.

Two ways to compute the vectors:

    # the real local model behind its OpenAI-compatible endpoint (VllmEmbeddings)
    uv run python scripts/record_eval_vectors.py --endpoint http://vllm.lan:8000/v1 \\
        --model BAAI/bge-m3 --dims 1024

    # the same model's ONNX export on CPU (needs onnxruntime, tokenizers and numpy in the
    # interpreter that runs it; not dependencies of the backend)
    python scripts/record_eval_vectors.py --onnx-dir /path/to/bge-m3/onnx --model BAAI/bge-m3

The ONNX mode takes the export's `sentence_embedding` output (the CLS vector) and
L2-normalises it, which is what bge-m3's dense embedding is.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "search_eval"
BATCH = 16
DECIMALS = 6
PAD_ID = 1  # bge-m3 (XLM-RoBERTa) <pad>

Embed = Callable[[Sequence[str]], list[list[float]]]


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def eval_texts(eval_dir: Path = EVAL_DIR) -> tuple[list[str], list[str]]:
    """(chunk texts as indexed, query texts), each in file order, without repeats."""
    chunks = [
        "\n".join([*chunk["heading_path"], chunk["text"]])
        for doc in _jsonl(eval_dir / "corpus.jsonl")
        for chunk in doc["chunks"]
    ]
    queries = [q["text"] for q in _jsonl(eval_dir / "queries.jsonl")]
    return list(dict.fromkeys(chunks)), list(dict.fromkeys(queries))


def onnx_embedder(onnx_dir: Path) -> Embed:
    """bge-m3's ONNX export on CPU: `sentence_embedding`, L2-normalised."""
    np: Any = importlib.import_module("numpy")
    ort: Any = importlib.import_module("onnxruntime")
    tokenizers: Any = importlib.import_module("tokenizers")
    session = ort.InferenceSession(str(onnx_dir / "model.onnx"), providers=["CPUExecutionProvider"])
    tokenizer = tokenizers.Tokenizer.from_file(str(onnx_dir / "tokenizer.json"))
    names = [o.name for o in session.get_outputs()]
    output = names.index("sentence_embedding") if "sentence_embedding" in names else 0

    def embed(texts: Sequence[str]) -> list[list[float]]:
        encoded = tokenizer.encode_batch(list(texts))
        width = max(len(e.ids) for e in encoded)
        ids = np.array([e.ids + [PAD_ID] * (width - len(e.ids)) for e in encoded], np.int64)
        mask = np.array([[1] * len(e.ids) + [0] * (width - len(e.ids)) for e in encoded], np.int64)
        out = session.run(None, {"input_ids": ids, "attention_mask": mask})[output]
        vectors = out[:, 0] if out.ndim == 3 else out  # noqa: PLR2004  # (batch, tokens, dims)
        vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        return [[float(x) for x in row] for row in vectors]

    return embed


def endpoint_embedder(endpoint: str, model: str, dims: int) -> Embed:
    """The model behind its OpenAI-compatible endpoint, through `VllmEmbeddings`."""
    from tumnis.core.clock import SystemClock  # noqa: PLC0415
    from tumnis.core.net import NetPolicy  # noqa: PLC0415
    from tumnis.modules.decisions.adapters.embeddings.vllm import (  # noqa: PLC0415
        VllmEmbeddings,
    )

    adapter = VllmEmbeddings(
        endpoint, model, dims, clock=SystemClock(), net_policy=NetPolicy(mode="self-hosted")
    )

    def embed(texts: Sequence[str]) -> list[list[float]]:
        return asyncio.run(adapter.embed(texts))

    return embed


def lines(texts: Sequence[str], model: str, embed: Embed) -> list[str]:
    out: list[str] = []
    for start in range(0, len(texts), BATCH):
        batch = texts[start : start + BATCH]
        for text, vector in zip(batch, embed(batch), strict=True):
            row = {
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "model": model,
                "vector": [round(x, DECIMALS) for x in vector],
            }
            out.append(json.dumps(row, separators=(",", ":")))
    return out


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--endpoint", help="OpenAI-compatible API root, e.g. http://host:8000/v1")
    source.add_argument("--onnx-dir", type=Path, help="the model's ONNX export folder")
    parser.add_argument("--model", required=True)
    parser.add_argument("--dims", type=int, default=1024)
    parser.add_argument("--eval-dir", type=Path, default=EVAL_DIR)
    args = parser.parse_args(argv)
    embed = (
        onnx_embedder(args.onnx_dir)
        if args.onnx_dir is not None
        else endpoint_embedder(args.endpoint, args.model, args.dims)
    )
    chunks, queries = eval_texts(args.eval_dir)
    target = args.eval_dir / "vectors"
    target.mkdir(exist_ok=True)
    for name, texts in (("chunks.jsonl", chunks), ("queries.jsonl", queries)):
        (target / name).write_text("\n".join(lines(texts, args.model, embed)) + "\n")
        print(f"{name}: {len(texts)} vectors")


if __name__ == "__main__":
    main()
