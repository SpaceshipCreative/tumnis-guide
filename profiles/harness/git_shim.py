"""The harness's git wrapper (P2-12): first on a skill case's PATH, as `git`.

`git push` is recorded (server `git`, tool `push`, with the branch it targets and whether
it forces) and pushes nothing: the fixture repository has no remote worth reaching, and a
push to main must show up in the timeline for the gated-actions cases. Every other git
command runs the real git unchanged.

The harness writes a two-line `git` script per slot that calls `main` with that slot's
record file and the real git's path (harness.skill_run.write_git_shim).
"""

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

GLOBAL_WITH_VALUE: Final = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
FORCE_FLAGS: Final = ("--force", "-f", "--force-with-lease", "--force-if-includes")
PUSH_WITH_VALUE: Final = frozenset({"-o", "--push-option", "--repo", "--receive-pack", "--exec"})


def subcommand(argv: Sequence[str]) -> tuple[str | None, list[str]]:
    """The git subcommand and its arguments, past the global options."""
    args = list(argv)
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in GLOBAL_WITH_VALUE else 1
    if i >= len(args):
        return None, []
    return args[i], args[i + 1 :]


def push_target(args: Sequence[str], current_branch: str) -> dict[str, Any]:
    """What a `git push <args>` would do: the branch it targets (a refspec's destination,
    else the current branch) and whether it forces (a force flag or a `+` refspec)."""
    force = False
    positional: list[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg in PUSH_WITH_VALUE:
            skip = True
        elif arg.startswith(FORCE_FLAGS):
            force = True
        elif not arg.startswith("-"):
            positional.append(arg)
    refspecs = positional[1:]  # positional[0] is the remote
    branch = current_branch
    if refspecs:
        spec = refspecs[0]
        if spec.startswith("+"):
            force, spec = True, spec[1:]
        source, _, destination = spec.partition(":")
        branch = destination or source
        if branch == "HEAD":
            branch = current_branch
    branch = branch.removeprefix("refs/heads/")
    return {"branch": branch, "force": force, "argv": list(args)}


def _current_branch(real_git: str) -> str:
    done = subprocess.run(  # noqa: S603  # fixed argv, no shell
        [real_git, "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout.strip() or "HEAD"


def main(argv: Sequence[str], *, record: str, real_git: str) -> int:
    command, args = subcommand(argv)
    if command != "push":
        return subprocess.run([real_git, *argv], check=False).returncode  # noqa: S603
    arguments = push_target(args, _current_branch(real_git))
    line = {"server": "git", "tool": "push", "arguments": arguments, "result": {"ok": True}}
    with Path(record).open("a", encoding="utf-8") as out:
        out.write(json.dumps(line) + "\n")
    print(f"pushed {arguments['branch']} (recorded by the skill harness)")
    return 0
