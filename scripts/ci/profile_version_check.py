#!/usr/bin/env python3
"""profile_version_check: a change to a Hermes profile needs a VERSION bump (P1-05, FR-5.10).

A profile is a directory `profiles/<name>/` with a VERSION file. Installed profiles are
updated by version, so when a diff changes anything under one, its VERSION must change
too, to a higher `X.Y.Z`. A profile that is new in the diff, or deleted by it, passes.
Directories without a VERSION (the harness, the cases, shared/) are not profiles.

    uv run --project backend python scripts/ci/profile_version_check.py --base origin/main
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _tests_extract import git, show

REPO = Path(__file__).resolve().parents[2]
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def changed_profiles(repo: Path, base: str, head: str) -> set[str]:
    """Names of the `profiles/<name>/` directories the diff base...head touches."""
    raw = git(repo, "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}").stdout
    names: set[str] = set()
    for path in filter(None, raw.split("\0")):
        parts = PurePosixPath(path).parts
        if len(parts) >= 3 and parts[0] == "profiles":  # noqa: PLR2004  # profiles/<name>/<file>
            names.add(parts[1])
    return names


def _version(text: str | None) -> tuple[int, int, int] | None:
    match = SEMVER.match((text or "").strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def problems(repo: Path, base: str, head: str) -> list[str]:
    fork = git(repo, "merge-base", base, head).stdout.strip()
    found: list[str] = []
    for name in sorted(changed_profiles(repo, base, head)):
        version_file = f"profiles/{name}/VERSION"
        after = show(repo, head, version_file)
        before = show(repo, fork, version_file)
        if after is None or before is None:
            continue  # not a profile at head (deleted, or never one), or new in this diff
        new, old = _version(after), _version(before)
        if new is None:
            found.append(f"{version_file}: {after.strip()!r} is not X.Y.Z")
        elif after.strip() == before.strip():
            found.append(f"profiles/{name}/ changed but {version_file} is still {_text(old)}")
        elif old is not None and new <= old:
            found.append(f"{version_file}: {after.strip()} is not above {before.strip()}")
    return found


def _text(version: tuple[int, int, int] | None) -> str:
    return ".".join(map(str, version)) if version else "unset"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--base", required=True, help="the PR base (a commit or ref)")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--repo", type=Path, default=REPO)
    args = parser.parse_args(argv)
    found = problems(args.repo, args.base, args.head)
    for problem in found:
        print(f"profile-version: {problem}", file=sys.stderr)
    if found:
        print("Bump the profile's VERSION (and distribution.yaml's version).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
