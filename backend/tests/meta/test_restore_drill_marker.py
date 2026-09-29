"""The restore drill's recovery target (P0-28, REL-1): scripts/drill/restore_drill.sh runs
against a fake `docker` (and `sleep`) on PATH that answers like a source Postgres with
pgBackRest, on a fake clock, so the timing a real stack hits by chance is fixed here."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DRILL = REPO / "scripts" / "drill" / "restore_drill.sh"
BACKUP_STOP = int(datetime(2026, 3, 9, 12, 0, tzinfo=UTC).timestamp())

# The fake docker: the compose, inspect and exec calls the drill makes up to its fence
# (step 3), which it refuses so the drill stops there. The source database's clock lives in
# a file; every psql call takes a little time and the fake `sleep` moves it forward.
FAKE_DOCKER = r"""
import os, sys, uuid
from datetime import UTC, datetime

args = sys.argv[1:]
state = os.environ["FAKE_STATE"]
clock_file = os.path.join(state, "clock")


def clock(advance=0.0):
    now = float(open(clock_file).read()) + advance
    open(clock_file, "w").write(repr(now))
    return now


if args[:1] == ["compose"]:
    if "ps" in args:
        print("pg-container" if args[-1] == "postgres" else "api-container")
    sys.exit(0)
if args[:1] == ["inspect"]:
    print("sha256:image")
    sys.exit(0)
if args[:2] == ["image", "inspect"]:
    sys.exit(0)
if args[:1] == ["exec"] and "pgbackrest" in args:
    print(os.environ["FAKE_INFO"])
    sys.exit(0)
if args[:1] == ["exec"] and "psql" in args:
    sql = args[args.index("-c") + 1]
    now = clock(0.05)
    if "INSERT INTO ops_drill_markers" in sql:
        if "'fence'" in sql:
            open(os.path.join(state, "fence_attempted"), "w").write(sql)
            sys.exit(3)  # the test ends the drill here
        print(uuid.uuid4())
    elif "to_char(clock_timestamp()" in sql:
        at = datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
        print(f"{at}+00|000000010000000000000003")
    elif "extract(epoch FROM clock_timestamp())" in sql:
        print(int(now) if "floor(" in sql else now)
    else:
        sys.stderr.write(f"fake docker: unexpected SQL {sql!r}\n")
        sys.exit(2)
    sys.exit(0)
sys.exit(0)  # rm, volume rm, network rm on cleanup
"""

FAKE_SLEEP = r"""
import os, sys

clock_file = os.path.join(os.environ["FAKE_STATE"], "clock")
now = float(open(clock_file).read()) + float(sys.argv[1])
open(clock_file, "w").write(repr(now))
"""


def _install(bin_dir: Path, name: str, source: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!{sys.executable}\n{source}")
    path.chmod(0o755)


@pytest.mark.req("REL-1")
@pytest.mark.wp("P0-28")
def test_issue_37_marker_is_taken_after_a_completed_backup_stopped(tmp_path: Path) -> None:
    """The drill's recovery target is in a later second than a completed repo2 backup's stop
    time. pgBackRest restores from the newest backup whose stop time (whole seconds) is
    strictly before the target, so a marker taken in the second the first backup stopped
    has no backup set to restore from ("unable to find backup set with stop time less
    than"), and step 6 failed."""
    bin_dir, state = tmp_path / "bin", tmp_path / "state"
    bin_dir.mkdir()
    state.mkdir()
    _install(bin_dir, "docker", FAKE_DOCKER)
    _install(bin_dir, "sleep", FAKE_SLEEP)
    # The first full backup has just finished: the database clock is in its stop second.
    (state / "clock").write_text(repr(BACKUP_STOP + 0.4))
    info = [
        {
            "name": "tumnis",
            "backup": [
                {
                    "label": "20260309-115940F",
                    "type": "full",
                    "timestamp": {"start": BACKUP_STOP - 20, "stop": BACKUP_STOP},
                }
            ],
            "archive": [{"min": "000000010000000000000001", "max": "000000010000000000000002"}],
        }
    ]
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_STATE": str(state),
        "FAKE_INFO": json.dumps(info),
        "DRILL_PROJECT": "tumnis-drill-fake",
    }

    run = subprocess.run(  # noqa: S603 (fixed argv)
        ["bash", str(DRILL), "--mode", "rehearsal"],  # noqa: S607 (bash from PATH)
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    marker = re.search(r"2 marker \S+ at (\S+ \S+)\+00", run.stderr)
    assert marker, run.stderr
    target = datetime.strptime(marker.group(1), "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC)
    assert int(target.timestamp()) > BACKUP_STOP, (
        f"target {marker.group(1)} is in the second the backup stopped ({BACKUP_STOP})"
    )
    # The drill reached its fence insert (so it didn't exit early), and nothing ran past it.
    assert (state / "fence_attempted").exists(), run.stderr
    assert "3 fence" not in run.stderr
