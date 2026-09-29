"""pytest plugin for traceability: dump every collected test's tags at collection time.

    TRACE_JSON=trace.json pytest --collect-only -q -p pytest_trace   (scripts/ci on PYTHONPATH)

Writes a list of {id, reqs, wp, tid, expected_failure}: `req` marker args, the `wp`
marker, the first docstring line (the T-ID) and whether the test is a spec xfail or an
unconditional skip, which traceability does not count as proof. Collection needs no
database, so this runs on any machine.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest


def _first_doc_line(item: pytest.Item) -> str | None:
    doc = getattr(getattr(item, "function", None), "__doc__", None)
    lines = [line.strip() for line in (doc or "").strip().splitlines()]
    return lines[0] if lines and lines[0] else None


def _expected_failure(item: pytest.Item) -> bool:
    spec_xfail = any(
        str(mark.kwargs.get("reason", "")).startswith("spec:")
        for mark in item.iter_markers("xfail")
    )
    skipped = item.get_closest_marker("skip") is not None  # skipif is conditional; ignored
    return spec_xfail or skipped


def ref(item: pytest.Item) -> dict[str, Any]:
    wp = item.get_closest_marker("wp")
    return {
        "id": item.nodeid,
        "reqs": sorted({str(arg) for mark in item.iter_markers("req") for arg in mark.args}),
        "wp": str(wp.args[0]) if wp and wp.args else None,
        "tid": _first_doc_line(item),
        "expected_failure": _expected_failure(item),
    }


def pytest_collection_finish(session: pytest.Session) -> None:
    target = Path(os.environ.get("TRACE_JSON", "trace.json"))
    target.write_text(json.dumps([ref(item) for item in session.items], indent=1))
