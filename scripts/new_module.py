"""Create a backend module skeleton with the A2 shape.

Usage: python scripts/new_module.py <name> [<name> ...]

Also registers the module: appends it to `MODULES` in `tumnis/core/modules.py`,
to the `modules-api-only` independence contract in `backend/.importlinter`, and
to the forbidden list of `search-usage-subscribe-only`.

Idempotent: existing files and registrations are left alone, so it is safe to
re-run on a module that already has code.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODULES_DIR = REPO / "backend" / "tumnis" / "modules"
REGISTRY = REPO / "backend" / "tumnis" / "core" / "modules.py"
IMPORTLINTER = REPO / "backend" / ".importlinter"
SUBSCRIBE_ONLY = frozenset({"search", "usage"})

FILE_DOCSTRINGS = {
    "__init__.py": "{name} module. Keep empty: other modules import `{name}.api` only.",
    "api.py": "{name} public functions and DTOs; the only file other modules may import.",
    "router.py": "{name} FastAPI router under /v1/{name}; thin calls into api.py.",
    "mcp.py": "{name} MCP tools; thin calls into api.py.",
    "models.py": "{name} SQLAlchemy tables owned by this module.",
    "rules.py": "{name} pure rules: no I/O, `now` and `tz` passed in.",
    "workflows.py": "{name} DBOS workflows and steps.",
    "events.py": "{name} event payload models and subscribers.",
}
PACKAGE_DIRS = {
    "adapters": "{name} adapters: one file per outside dependency, each with a fake.",
    "migrations": "{name} Alembic revisions for this module's tables.",
    "tests": "{name} tests.",
    "tests/unit": "{name} unit tests: rules and pure code, sockets disabled.",
    "tests/integration": "{name} integration tests: Postgres, DBOS, containers.",
    "tests/contract": "{name} contract tests: adapters, connectors, schemas.",
}
DATA_DIRS = ("tests/recordings",)
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _write_new(path: Path, content: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return True


def create_skeleton(name: str) -> list[Path]:
    """Write every missing skeleton file for `name`; return the paths created."""
    created: list[Path] = []
    root = MODULES_DIR / name
    if _write_new(MODULES_DIR / "__init__.py", '"""Feature modules (A2 shape)."""\n'):
        created.append(MODULES_DIR / "__init__.py")
    for filename, doc in FILE_DOCSTRINGS.items():
        path = root / filename
        if _write_new(path, f'"""{doc.format(name=name)}"""\n'):
            created.append(path)
    for dirname, doc in PACKAGE_DIRS.items():
        path = root / dirname / "__init__.py"
        if _write_new(path, f'"""{doc.format(name=name)}"""\n'):
            created.append(path)
    for dirname in DATA_DIRS:
        path = root / dirname / ".gitkeep"
        if _write_new(path, ""):
            created.append(path)
    return created


def register_in_registry(name: str) -> bool:
    """Append `name` to the MODULES tuple; return True when the file changed."""
    text = REGISTRY.read_text()
    match = re.search(r"^MODULES: Final\[tuple\[str, \.\.\.\]\] = \((.*?)^\)", text, re.S | re.M)
    if match is None:
        raise SystemExit(f"MODULES tuple not found in {REGISTRY}")
    if f'"{name}"' in match.group(1):
        return False
    REGISTRY.write_text(text[: match.end(1)] + f'    "{name}",\n' + text[match.end(1) :])
    return True


def _append_to_list(text: str, section: str, key: str, entry: str) -> tuple[str, bool]:
    """Append `entry` to the multi-line `key =` list inside `[section]` of an ini file."""
    pattern = rf"(^\[{re.escape(section)}\]\n(?:(?!^\[).*\n)*?^{key} =\n(?:^[ \t]+\S.*\n)*)"
    match = re.search(pattern, text, re.M)
    if match is None:
        raise SystemExit(f"[{section}] {key} not found in {IMPORTLINTER}")
    if re.search(rf"^[ \t]+{re.escape(entry)}$", match.group(1), re.M):
        return text, False
    return text[: match.end(1)] + f"    {entry}\n" + text[match.end(1) :], True


def register_in_contracts(name: str) -> bool:
    """Add `name` to the independence contract and the subscribe-only forbidden list."""
    text = IMPORTLINTER.read_text()
    entry = f"tumnis.modules.{name}"
    text, changed = _append_to_list(
        text, "importlinter:contract:modules-api-only", "modules", entry
    )
    if name not in SUBSCRIBE_ONLY:
        text, forbidden_changed = _append_to_list(
            text, "importlinter:contract:search-usage-subscribe-only", "forbidden_modules", entry
        )
        changed = changed or forbidden_changed
    if changed:
        IMPORTLINTER.write_text(text)
    return changed


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    for name in argv:
        if not NAME_RE.match(name):
            print(f"invalid module name: {name!r}", file=sys.stderr)
            return 2
        for path in create_skeleton(name):
            print(f"created {path.relative_to(REPO)}")
        if register_in_registry(name):
            print(f"registered {name} in {REGISTRY.relative_to(REPO)}")
        if register_in_contracts(name):
            print(f"registered {name} in {IMPORTLINTER.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
