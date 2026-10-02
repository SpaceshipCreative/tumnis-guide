#!/usr/bin/env python3
"""Release asset check (P4-06, SEC-7, SAAS-1): a release carries its CycloneDX SBOM, its
OpenAPI spec and its JSON Schemas, none of them empty.

The release job runs it after `gh release create`, on what GitHub now holds:

    gh release view v1.0.0 --json assets > assets.json
    python3 scripts/release/verify_assets.py v1.0.0 assets.json   # exit 1 naming each gap

Standard library only, like check_changelog.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


def required_assets(tag: str) -> list[str]:
    """The files every release attaches (release.yml): SBOM, OpenAPI spec, schemas."""
    return ["sbom.cdx.json", f"openapi-{tag}.json", f"schemas-{tag}.tar.gz"]


def missing_assets(tag: str, assets: Iterable[Mapping[str, Any]]) -> list[str]:
    """The required assets that are absent or empty, in `required_assets` order."""
    sizes = {str(asset.get("name")): int(asset.get("size") or 0) for asset in assets}
    return [name for name in required_assets(tag) if sizes.get(name, 0) <= 0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("tag", help="the release tag, for example v1.0.0")
    parser.add_argument("assets", type=Path, help="`gh release view <tag> --json assets` output")
    args = parser.parse_args(argv)
    assets = json.loads(args.assets.read_text(encoding="utf-8")).get("assets") or []
    missing = missing_assets(args.tag, assets)
    if missing:
        print(f"verify_assets: {args.tag} lacks {', '.join(missing)}", file=sys.stderr)
        return 1
    print(f"verify_assets: {args.tag} has {', '.join(required_assets(args.tag))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
