#!/usr/bin/env python3
"""Record Jev's answers for the decisions contract suite (P1-01). Manual; never in CI.

Re-asks the real Jev API for every recording in
backend/tumnis/modules/decisions/tests/recordings/jev/ (or the points named), using the
recording's stored `inputs`, and rewrites its `request`, `response`, `latency_ms`,
`recorded_at` and `sdk_version`. It spends Jev credits: run it by hand, once per model
release, and review the diff before committing.

    TYPESAFE_API_KEY=... uv run --directory backend python ../scripts/record_jev.py --point all

Scrubbing: the inputs are invented already (example.com / example.org names and
addresses) and are sent as they are; from the response only `model`, `usage` and
`answers` are kept, and only the allow-listed headers (the request id is replaced).
Recordings whose response is not a 200 (the rate-limit case) cannot be produced on demand
and are left as they are.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typesafe_sdk
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

REPO = Path(__file__).resolve().parents[1]
RECORDINGS = REPO / "backend/tumnis/modules/decisions/tests/recordings/jev"
PINNED_MODEL = "jev-1.13.0"
HEADER_ALLOW_LIST = frozenset({"retry-after", "retry-after-ms"})
BODY_ALLOW_LIST = ("model", "usage", "answers")
NOTES = (
    "Recorded with scripts/record_jev.py against the real Jev API. Inputs are invented "
    "(example.com/example.org); only model, usage and answers are kept from the response "
    "body, and the request id is replaced."
)


async def record(
    name: str, rec: dict[str, Any], client: AsyncTypeSafeClient, model: str, n: int
) -> dict[str, Any] | None:
    """The recording re-asked of Jev, or None when it cannot be recorded on demand."""
    from tumnis.modules.decisions.adapters.jev import _to_sdk, wire_body  # noqa: PLC0415
    from tumnis.modules.decisions.catalog import DecisionPoint, build_request  # noqa: PLC0415

    if rec["response"]["status"] != 200:  # noqa: PLR2004
        print(f"skip {name}: a {rec['response']['status']} cannot be recorded on demand")
        return None
    req = build_request(DecisionPoint(rec["point"]), rec["inputs"])
    questions = {qid: _to_sdk(q) for qid, q in req.questions.items()}
    started = time.monotonic()
    resp = await client.system_one(req.state, questions, model=model)
    print(f"{name}: answered by {resp.model}")
    latency_ms = int((time.monotonic() - started) * 1000)
    raw = resp.raw_http_response
    body = raw.json()
    headers = {k: v for k, v in raw.headers.items() if k.lower() in HEADER_ALLOW_LIST}
    headers["x-typesafe-request-id"] = f"req_recorded_{n:04d}"
    return rec | {
        "request": wire_body(req, model=model),
        "response": {
            "status": raw.status_code,
            "headers": headers,
            "body": {key: body[key] for key in BODY_ALLOW_LIST if key in body},
        },
        "latency_ms": latency_ms,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sdk_version": typesafe_sdk.__version__,
        "synthetic": False,
        "notes": NOTES,
    }


async def ask_all(
    recordings: dict[Path, dict[str, Any]], key: str, model: str
) -> dict[Path, dict[str, Any] | None]:
    async with AsyncTypeSafeClient(
        api_key=key, model=model, retry=RetryPolicy(max_retries=0)
    ) as client:
        return {
            path: await record(path.name, rec, client, model, n)
            for n, (path, rec) in enumerate(recordings.items(), start=1)
        }


def main(points: list[str], model: str) -> int:
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key:
        print("TYPESAFE_API_KEY is not set", file=sys.stderr)
        return 2
    sys.path.insert(0, str(REPO / "backend"))
    recordings = {path: json.loads(path.read_text()) for path in sorted(RECORDINGS.glob("*.json"))}
    chosen = {
        path: rec for path, rec in recordings.items() if "all" in points or rec["point"] in points
    }
    updated = asyncio.run(ask_all(chosen, key, model))
    for path, rec in updated.items():
        if rec is not None:
            path.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
            print(f"recorded {path.name}: {rec['response']['body'].get('model')}")
    done = sum(rec is not None for rec in updated.values())
    print(f"{done} recorded, {len(updated) - done} left as they were")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--point", action="append", default=[], help="a point id, or all")
    parser.add_argument("--model", default=PINNED_MODEL, help="the pinned model version")
    args = parser.parse_args()
    if not args.point:
        parser.error("name --point all or at least one point")
    sys.exit(main(args.point, args.model))
