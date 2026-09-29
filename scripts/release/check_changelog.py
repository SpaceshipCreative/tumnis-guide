#!/usr/bin/env python3
"""Release tag and changelog check (P0-30, REL-4).

A release tag is `v` followed by a SemVer 2.0.0 version (https://semver.org), and
CHANGELOG.md (Keep a Changelog 1.1.0) has a dated section for it:

    ## [0.1.0] - 2026-10-02

    python scripts/release/check_changelog.py v0.1.0             # exit 1 with the reason
    python scripts/release/check_changelog.py --extract v0.1.0   # the section, for notes

Standard library only: the release job runs it before installing anything.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"

# semver.org's recommended pattern, with the `v` prefix the tags carry.
SEMVER_TAG = re.compile(
    r"^v(?P<version>(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?)$"
)
SECTION = re.compile(r"^## \[(?P<name>[^\]]+)\](?:\s+-\s+(?P<date>\S+))?\s*$", re.MULTILINE)
LINK_REFERENCE = re.compile(r"^\[[^\]]+\]:\s+\S+\s*$", re.MULTILINE)


class ReleaseError(ValueError):
    """The tag or the changelog is not ready for a release."""


def version_of(tag: str) -> str:
    match = SEMVER_TAG.match(tag)
    if match is None:
        raise ReleaseError(f"{tag!r} is not a release tag: expected v<MAJOR>.<MINOR>.<PATCH>")
    return match.group("version")


def section(text: str, version: str) -> str:
    """The body of `## [version] - YYYY-MM-DD`, up to the next section or the link
    references at the end."""
    headings = list(SECTION.finditer(text))
    for i, heading in enumerate(headings):
        if heading.group("name") != version:
            continue
        released = heading.group("date")
        if released is None:
            raise ReleaseError(f"CHANGELOG.md: section [{version}] has no release date")
        try:
            date.fromisoformat(released)
        except ValueError as exc:
            raise ReleaseError(f"CHANGELOG.md: [{version}] date {released!r} is not ISO") from exc
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        body = LINK_REFERENCE.sub("", text[heading.end() : end]).strip()
        if not body:
            raise ReleaseError(f"CHANGELOG.md: section [{version}] is empty")
        return body + "\n"
    raise ReleaseError(f"CHANGELOG.md has no section ## [{version}] - YYYY-MM-DD")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("tag", help="the release tag, for example v0.1.0")
    parser.add_argument("--extract", action="store_true", help="print the section's body")
    parser.add_argument("--changelog", type=Path, default=CHANGELOG)
    args = parser.parse_args(argv)
    try:
        body = section(args.changelog.read_text(encoding="utf-8"), version_of(args.tag))
    except ReleaseError as exc:
        print(f"check_changelog: {exc}", file=sys.stderr)
        return 1
    if args.extract:
        sys.stdout.write(body)
    else:
        print(f"check_changelog: {args.tag} ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
