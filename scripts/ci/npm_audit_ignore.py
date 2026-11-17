#!/usr/bin/env python3
"""npm_audit_ignore: decide whether a failed `npm audit` blocks the merge (P0-16, SEC-7).

`npm audit` has no ignore list. The Security job saves its JSON report and, when npm exits
non-zero, runs this on it: a high or critical advisory blocks unless `.npmauditignore`
lists its GHSA id with an `exp:YYYY-MM-DD` that has not passed. Anything that is not a
report blocks. Entries follow the `.trivyignore` rule (issue link, expiry at most 30 days
out); backend/tests/meta/test_security_job.py checks the format. Standard library only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

IGNORE_FILE = Path(__file__).resolve().parents[2] / ".npmauditignore"
ENTRY = re.compile(r"^(?P<id>GHSA-[\w-]+)\b.*\bexp:(?P<exp>\d{4}-\d{2}-\d{2})\b")
BLOCKING = {"high", "critical"}


def ignored(text: str, today: dt.date) -> set[str]:
    """The advisory ids whose entry has an expiry that has not passed."""
    entries = (ENTRY.match(line.strip()) for line in text.splitlines())
    return {e["id"] for e in entries if e and dt.date.fromisoformat(e["exp"]) >= today}


def main(argv: list[str] | None = None, *, today: dt.date | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="the saved `npm audit --json` output")
    parser.add_argument("--ignore-file", type=Path, default=IGNORE_FILE)
    args = parser.parse_args(argv)
    try:
        packages = json.loads(args.report.read_text())["vulnerabilities"]
        # A package's `via` holds advisories (objects) and the names of the packages it is
        # affected through (strings); only the advisories count.
        found = {
            via["url"].rsplit("/", 1)[-1]: f"{via['severity']}: {via['title']} ({via['url']})"
            for package in packages.values()
            for via in package["via"]
            if isinstance(via, dict) and via["severity"] in BLOCKING
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"npm audit gave no report, so it blocks: {error!r}", file=sys.stderr)
        return 2
    skip = ignored(args.ignore_file.read_text(), today or dt.datetime.now(dt.UTC).date())
    for ghsa, line in sorted(found.items()):
        print(("ignored" if ghsa in skip else "BLOCKS"), line)
    return 1 if found.keys() - skip else 0


if __name__ == "__main__":
    sys.exit(main())
