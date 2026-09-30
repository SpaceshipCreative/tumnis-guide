"""scripts/backup/folders.sh: the nightly folder backup job (P1-15, REL-1). It reads the
manifest from `tumnis knowledge backup-sources --json` and copies each source to its own
destination under the backup remote, through deploy/rclone/folders-backup.sh.

Collected from backend/ (pyproject testpaths lists ../scripts/backup/tests).
"""

# ruff: noqa: S101

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
JOB = HERE.parent / "folders.sh"
SEP = "\x1f"
# Subcommands that remove files at the destination (or the source): never allowed here.
DESTRUCTIVE = {"sync", "bisync", "move", "moveto", "purge", "delete", "deletefile", "rmdirs"}

SOURCES = [
    {"source": "/data/projects/Acme", "dest": "ws-1/p-1", "mode": "tumnis_made"},
    {"source": "/data/projects/Site redesign", "dest": "ws-1/p-2", "mode": "tumnis_made"},
    {"source": "tumnis-loc-9:bucket/tumnis/Opted", "dest": "ws-2/p-3", "mode": "existing"},
]


def _stub(folder: Path, name: str, body: str) -> None:
    path = folder / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


@pytest.mark.req("REL-1")
@pytest.mark.wp("P1-15")
@pytest.mark.xfail(strict=True, reason="spec:P1-15")
def test_uses_rclone_copy_never_sync(tmp_path: Path) -> None:
    """T-P1-15-12
    The job runs one copy per manifest source, with checksums, from the source to
    `<remote>/<dest>`, and never a subcommand that deletes at the destination; a source
    with spaces stays one argument.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    manifest = json.dumps(SOURCES)
    _stub(bin_dir, "tumnis", f"cat <<'EOF'\n{manifest}\nEOF\n")
    # Each call of the stub is one line: its arguments joined by \x1f.
    _stub(bin_dir, "rclone", f'( IFS="$(printf "\\037")"; printf "%s\\n" "$*" ) >> "{calls}"\n')
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}

    done = subprocess.run(  # noqa: S603  # our own script, fixed arguments
        ["/bin/sh", str(JOB), "b2:tumnis-folders"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr

    invocations = [line.split(SEP) for line in calls.read_text().splitlines()]
    assert len(invocations) == len(SOURCES)
    for argv, src in zip(invocations, SOURCES, strict=True):
        assert argv[0] == "copy"
        assert "--checksum" in argv
        assert argv[-2:] == [src["source"], f"b2:tumnis-folders/{src['dest']}"]
        assert not DESTRUCTIVE & set(argv)
