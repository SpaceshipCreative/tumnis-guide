#!/usr/bin/env python3
"""squawk over the expand revisions a change touches (P0-06, REL-4).

Every Alembic revision declares `phase = "expand"` (additive, safe while the previous
release still runs) or `phase = "contract"` (removes what the previous release needed).
For each changed revision file under a version location, this renders the SQL that
revision's `upgrade()` emits (offline, as `alembic upgrade <down>:<rev> --sql` would,
without the version-table bookkeeping and with env.py's `SET lock_timeout`) and runs
squawk on it with the repo's `.squawk.toml`. Contract revisions are skipped: dropping is
what they are for. A revision without a valid phase fails.

    cd backend && uv run python ../scripts/ci/squawk_migrations.py --changed-since origin/main
    cd backend && uv run python ../scripts/ci/squawk_migrations.py path/to/revision.py ...
"""

from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import Script, ScriptDirectory

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
ALEMBIC_INI = BACKEND / "alembic.ini"
SQUAWK_CONFIG = REPO / ".squawk.toml"
# The same session setting env.py emits before every upgrade.
PREAMBLE = "SET lock_timeout = '5s';\n\n"

Status = Literal["passed", "rejected", "skipped"]


@dataclass(frozen=True)
class Checked:
    revision: str
    path: Path
    phase: str
    status: Status
    sql: str = ""
    output: str = ""


def script_directory(extra_locations: Sequence[Path] = ()) -> ScriptDirectory:
    """alembic.ini's revisions plus any extra version locations (the test fixtures)."""
    cfg = Config(str(ALEMBIC_INI))
    configured = (cfg.get_main_option("version_locations") or "").split()
    locations = [*configured, *(str(p.resolve()) for p in extra_locations)]
    cfg.set_main_option("version_locations", "\n".join(locations))
    return ScriptDirectory.from_config(cfg)


def render_upgrade(script: Script) -> str:
    """The SQL the revision's upgrade() emits, rendered offline with literal values."""
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": buffer, "literal_binds": True},
    )
    with Operations.context(context):
        script.module.upgrade()
    return PREAMBLE + buffer.getvalue()


def squawk_binary() -> str:
    found = shutil.which("squawk") or shutil.which("squawk", path=str(Path(sys.executable).parent))
    if found is None:
        raise FileNotFoundError("squawk is not installed (uv sync installs squawk-cli)")
    return found


def run_squawk(sql: str, name: str) -> tuple[int, str]:
    result = subprocess.run(  # noqa: S603  # our pinned squawk binary, SQL on stdin
        [
            squawk_binary(),
            "--config",
            str(SQUAWK_CONFIG),
            "--reporter",
            "gcc",
            "--stdin-filepath",
            name,
        ],
        input=sql,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def check(files: Sequence[Path], extra_locations: Sequence[Path] = ()) -> list[Checked]:
    """Check the revisions defined in `files` (other files are ignored)."""
    wanted = {p.resolve() for p in files}
    results: list[Checked] = []
    for script in script_directory(extra_locations).walk_revisions():
        path = Path(script.path).resolve()
        if path not in wanted:
            continue
        phase = getattr(script.module, "phase", None)
        if phase == "contract":
            results.append(Checked(script.revision, path, phase, "skipped"))
            continue
        if phase != "expand":
            message = f'phase must be "expand" or "contract", found {phase!r}'
            results.append(Checked(script.revision, path, str(phase), "rejected", "", message))
            continue
        sql = render_upgrade(script)
        code, output = run_squawk(sql, f"{script.revision}.sql")
        status: Status = "passed" if code == 0 else "rejected"
        results.append(Checked(script.revision, path, phase, status, sql, output))
    return sorted(results, key=lambda r: str(r.path))


def changed_revision_files(base: str) -> list[Path]:
    """Revision files added or modified since `base` (merge base), under any version
    location."""
    locations = [Path(p).resolve() for p in script_directory().version_locations]
    diff = subprocess.run(  # noqa: S603
        ["git", "diff", "--name-only", "--diff-filter=d", f"{base}...HEAD"],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    changed = []
    for name in diff:
        path = (REPO / name).resolve()
        if path.suffix == ".py" and path.name != "__init__.py" and path.parent in locations:
            changed.append(path)
    return changed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("files", nargs="*", type=Path, help="revision files to check")
    parser.add_argument("--changed-since", metavar="REF", help="check revisions changed since REF")
    args = parser.parse_args(argv)
    files: list[Path] = list(args.files)
    if args.changed_since:
        files += changed_revision_files(args.changed_since)
    results = check(files)
    for result in results:
        print(f"{result.status:8} {result.phase:8} {result.revision}  {result.path}")
        if result.status == "rejected":
            print(result.output)
    if not results:
        print("no changed revisions")
    return 1 if any(r.status == "rejected" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
