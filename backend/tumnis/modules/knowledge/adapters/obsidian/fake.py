"""Fakes for the Obsidian vault readers (P3-12).

- `FakeVault`: the `VaultReader` port over an in-memory vault (`files`, path -> bytes),
  with the same listing rules as the real readers (sorted, `skip` asked first, sha256
  etags, `.git/` never listed) and a record of calls. `fake.script(path, bytes | None)`
  adds, changes or (None) removes a file.
- `FakeGitRunner`: stands in for git under `GitReader`. It records every argv list with
  its environment, and the deploy key file's content while the command ran (`key_seen`),
  answers from a script per subcommand (`runner.script("push", returncode=0)`), and on
  `clone` makes the target's `.git/` so the reader sees a clone.
"""

import asyncio
import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from tumnis.modules.knowledge.adapters.obsidian.git import CommandResult
from tumnis.modules.knowledge.adapters.obsidian.port import FileStat, Skip, never_skip
from tumnis.modules.knowledge.rules import PathRejected, safe_rel_path
from tumnis.modules.knowledge.storage import MAX_FILE_BYTES, NotFound, TooLarge

__all__ = ["FakeGitRunner", "FakeVault", "GitCall"]

T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


class FakeVault:
    def __init__(self, files: Mapping[str, bytes] | None = None) -> None:
        self.files: dict[str, bytes] = dict(files or {})
        self.calls: list[tuple[str, str | None]] = []

    def script(self, path: str, data: bytes | None) -> None:
        if data is None:
            self.files.pop(path, None)
        else:
            self.files[safe_rel_path(path)] = data

    async def list_files(self, skip: Skip = never_skip) -> list[FileStat]:
        self.calls.append(("list", None))
        out = []
        for path in sorted(self.files):
            parts = path.split("/")
            if parts[0] == ".git":
                continue
            folders = ["/".join(parts[: n + 1]) for n in range(len(parts) - 1)]
            if any(skip(folder) for folder in folders) or skip(path):
                continue
            data = self.files[path]
            out.append(
                FileStat(path=path, size=len(data), mtime=T0, etag=hashlib.sha256(data).hexdigest())
            )
        return out

    async def read(self, path: str) -> bytes:
        self.calls.append(("read", path))
        rel = safe_rel_path(path)
        if rel.split("/")[0] == ".git":
            raise PathRejected(f"{rel!r}: not vault content")
        if rel not in self.files:
            raise NotFound(rel)
        data = self.files[rel]
        if len(data) > MAX_FILE_BYTES:
            raise TooLarge(f"{rel!r} is over {MAX_FILE_BYTES} bytes")
        return data

    async def refresh(self) -> None:
        self.calls.append(("refresh", None))


@dataclass(frozen=True)
class GitCall:
    argv: list[str]
    env: dict[str, str]
    cwd: str | None
    key_seen: bytes | None  # the deploy key file's bytes while the command ran


_KEY_PATH = re.compile(r"(?:^|\s)-i\s+'?([^'\s]+)'?")


@dataclass
class FakeGitRunner:
    calls: list[GitCall] = field(default_factory=list)
    scripted: dict[str, CommandResult] = field(default_factory=dict)

    def script(self, verb: str, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.scripted[verb] = CommandResult(returncode=returncode, stdout=stdout, stderr=stderr)

    async def __call__(
        self, argv: Sequence[str], *, env: Mapping[str, str], cwd: str | None, timeout_s: float
    ) -> CommandResult:
        found = _KEY_PATH.search(env.get("GIT_SSH_COMMAND", ""))
        key_seen = await asyncio.to_thread(_read_if_file, found.group(1)) if found else None
        self.calls.append(GitCall(argv=list(argv), env=dict(env), cwd=cwd, key_seen=key_seen))
        verb = _verb(argv)
        result = self.scripted.get(verb, CommandResult(returncode=0))
        if verb == "clone" and result.returncode == 0:
            (Path(argv[-1]) / ".git").mkdir(parents=True, exist_ok=True)
        return result


def _read_if_file(path: str) -> bytes | None:
    file = Path(path)
    return file.read_bytes() if file.is_file() else None


def _verb(argv: Sequence[str]) -> str:
    rest = list(argv[1:])
    while rest and rest[0] in {"-c", "-C"}:
        rest = rest[2:]
    return rest[0] if rest else ""
