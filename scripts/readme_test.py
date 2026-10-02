#!/usr/bin/env python3
"""README harness (P4-06, A4.4, REL-4): run the README's tagged commands, block by block.

A shell block joins a suite through its fence info string. GitHub renders the first word
as the language and ignores the rest:

    ```bash readme:install:30 timeout=900 expect=^ok$
    docker compose up -d --build
    ```

| Token                    | Meaning                                                        |
| ------------------------ | -------------------------------------------------------------- |
| `readme:<suite>:<order>` | the suite (`install`, `first-run`, `upgrade`, `manual`, ...) and |
|                          | a sort key that must rise down the document within the suite  |
| `readme:env`             | KEY=value lines exported, literally, to every later block      |
| `timeout=<s>`            | the block's time limit (default 300 s)                         |
| `expect=<regex>`         | the block's output must match (no spaces: use \\s)             |

`manual` blocks are never run (installing Tailscale on a phone, the Coolify path); they are
listed in docs/INSTALL-CHECKLIST.md. Every shell block inside the README's Install section
must carry a tag (`check_section_coverage`), so no install step can drift untested.

Each block runs as `bash -euo pipefail -c <code>` in a fresh process, in the work directory
(the repository root, as if just cloned: the README's clone step is `manual`, and CI checks
the commit under test out instead), with the parent environment plus the env blocks'
exports. The harness substitutes nothing else: `{{x}}` and `${x}` reach bash as written.
The first block that fails, misses its `expect` or times out stops the run, and the report
names its README line.

    python3 scripts/readme_test.py --suite install --suite first-run \\
        --health-url https://localhost/health/ready --ca-file /etc/tumnis/https/tumnis.crt \\
        --report out/readme-report.json

With --health-url, the readiness check runs after the first suite: it polls until 200 (or
10 minutes) and then requires every critical and module check to be `ok`. Standard
library only: the README job runs it on a fresh VM before installing anything.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import signal
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
DEFAULT_TIMEOUT_S = 300  # plan default
MAX_FENCE_INDENT = 3  # CommonMark: a fence may be indented by up to three spaces
KILL_GRACE_S = 10  # after a timeout's SIGKILL, how long to wait for the pipe to close
HEALTH_TIMEOUT_S = 600  # A4.4: /health/ready within 10 minutes
SHELL_LANGS = frozenset({"bash", "sh", "shell", "console", "zsh"})
ENV_SUITE = "env"
# Checks that must be `ok` on a fresh install. `backups` is left out on purpose: it is
# degraded until the off-site repository is configured (docs/OPERATIONS.md, Backups).
CRITICAL_CHECKS = ("postgres", "dbos", "schema")

_FENCE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<info>[^`]*)$")
_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*#*\s*$")
_TAG = re.compile(r"^readme:(?P<suite>[a-z][a-z0-9-]*)(?::(?P<order>[^\s:]+))?$")
_ENV_LINE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")


@dataclass(frozen=True)
class Block:
    suite: str
    order: int
    lang: str
    code: str
    timeout: int
    expect: re.Pattern[str] | None
    line: int  # the opening fence's line in the document, 1-based


@dataclass(frozen=True)
class BlockResult:
    line: int
    suite: str
    order: int
    exit_code: int | None  # None: timed out
    seconds: float
    output: str
    error: str | None = None


@dataclass
class Report:
    results: list[BlockResult] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    failure: str = ""
    failed_line: int | None = None
    health: dict[str, Any] | None = None
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.failed_line is None and not self.failure

    def fail(self, line: int | None, message: str) -> None:
        self.failed_line = line
        self.failure = message

    def to_json(self) -> str:
        data = {
            "ok": self.ok,
            "failure": self.failure or None,
            "failed_line": self.failed_line,
            "seconds": round(self.seconds, 1),
            "health": self.health,
            "env": self.env,
            "results": [asdict(result) for result in self.results],
        }
        return json.dumps(data, indent=2)


# --- Parsing --------------------------------------------------------------------------------


def _closes(line: str, fence: str) -> bool:
    """CommonMark: a closing fence is the opener's character, at least as many of them,
    and nothing else on the line but whitespace, indented by at most three spaces."""
    body = line.rstrip()
    stripped = body.lstrip(" ")
    if len(body) - len(stripped) > MAX_FENCE_INDENT:  # four: indented code, not a fence
        return False
    return stripped.startswith(fence[0] * len(fence)) and set(stripped) == {fence[0]}


def _fences(markdown: str) -> Iterable[tuple[int, str, str, int]]:
    """(opening line, info string, code, closing line) for every fenced block."""
    lines = markdown.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        match = _FENCE.match(lines[i].rstrip("\n"))
        if match is None:
            i += 1
            continue
        fence = match.group("fence")
        start = i
        body: list[str] = []
        i += 1
        while i < len(lines):
            if _closes(lines[i], fence):
                break
            body.append(lines[i])
            i += 1
        yield start + 1, match.group("info").strip(), "".join(body), i + 1
        i += 1


def _block(line: int, info: str, code: str) -> Block | None:
    words = info.split()
    if not words:
        return None
    lang, tokens = words[0], words[1:]
    tags = [token for token in tokens if token.startswith("readme:")]
    if not tags:
        return None
    if len(tags) > 1:
        raise ValueError(f"line {line}: more than one readme: tag")
    tag = _TAG.match(tags[0])
    if tag is None:
        raise ValueError(f"line {line}: malformed tag {tags[0]!r}")
    suite, raw_order = tag.group("suite"), tag.group("order")
    if raw_order is None:
        order = 0
    elif raw_order.isdigit():
        order = int(raw_order)
    else:
        raise ValueError(f"line {line}: the order in {tags[0]!r} is not a number")
    timeout, expect = _options(line, [token for token in tokens if token not in tags])
    return Block(suite, order, lang, code, timeout, expect, line)


def _options(line: int, tokens: list[str]) -> tuple[int, re.Pattern[str] | None]:
    """`timeout=<s>` and `expect=<regex>`; anything else is refused with its line."""
    timeout, expect = DEFAULT_TIMEOUT_S, None
    for token in tokens:
        key, _, value = token.partition("=")
        if key == "timeout" and value.isdigit() and int(value) > 0:
            timeout = int(value)
        elif key == "expect" and value:
            try:
                expect = re.compile(value, re.MULTILINE)
            except re.error as exc:
                raise ValueError(f"line {line}: bad expect pattern {value!r}: {exc}") from None
        else:
            raise ValueError(f"line {line}: unknown or malformed token {token!r}")
    return timeout, expect


def extract(markdown: str) -> list[Block]:
    """Every tagged block, in document order. Raises ValueError (naming the line) on a
    malformed tag or token, or when a suite's orders do not rise down the document."""
    blocks = [
        block
        for line, info, code, _ in _fences(markdown)
        if (block := _block(line, info, code)) is not None
    ]
    last: dict[str, Block] = {}
    for block in blocks:
        if block.suite in (ENV_SUITE, "manual"):
            continue
        previous = last.get(block.suite)
        if previous is not None and block.order <= previous.order:
            raise ValueError(
                f"line {block.line}: readme:{block.suite}:{block.order} comes after "
                f"readme:{block.suite}:{previous.order} (line {previous.line}); "
                "orders must rise down the document"
            )
        last[block.suite] = block
    return blocks


def select(blocks: Sequence[Block], suite: str) -> list[Block]:
    """The suite's blocks with the env blocks, in document order (which is also the
    suite's order, `extract` checks)."""
    return [block for block in blocks if block.suite in (suite, ENV_SUITE)]


def section_lines(markdown: str, section: str) -> range:
    """The line numbers (1-based) of the section titled `section`, from its heading to
    the next heading of the same or a higher level. ValueError when there is none."""
    lines = markdown.splitlines()
    in_fence: str | None = None
    start = level = None
    for number, text in enumerate(lines, start=1):
        if in_fence is not None:
            if _closes(text, in_fence):
                in_fence = None
            continue
        opening = _FENCE.match(text)
        if opening is not None:
            in_fence = opening.group("fence")
            continue
        heading = _HEADING.match(text)
        if heading is None:
            continue
        depth = len(heading.group("hashes"))
        if start is None:
            if heading.group("title").strip().lower() == section.lower():
                start, level = number, depth
        elif level is not None and depth <= level:
            return range(start, number)
    if start is None:
        raise ValueError(f"no section titled {section!r}")
    return range(start, len(lines) + 1)


def check_section_coverage(markdown: str, section: str = "Install") -> list[int]:
    """Line numbers of untagged shell blocks inside the section; must be empty."""
    lines = section_lines(markdown, section)
    untagged = []
    for line, info, code, _ in _fences(markdown):
        if line not in lines:
            continue
        words = info.split()
        if words and words[0] in SHELL_LANGS and _block(line, info, code) is None:
            untagged.append(line)
    return untagged


def parse_env(block: Block) -> dict[str, str]:
    """A `readme:env` block's KEY=value lines, values taken literally. Blank lines and
    `#` comments are skipped; anything else is refused with its line."""
    exports = {}
    for offset, text in enumerate(block.code.splitlines(), start=1):
        stripped = text.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENV_LINE.match(stripped)
        if match is None:
            raise ValueError(f"line {block.line + offset}: not KEY=value: {stripped!r}")
        exports[match.group("key")] = match.group("value")
    return exports


# --- Running --------------------------------------------------------------------------------


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def run(blocks: Sequence[Block], env: Mapping[str, str], workdir: Path) -> Report:
    """Run each block in order, in its own `bash -euo pipefail` process in `workdir`, with
    `env` plus the exports of the env blocks seen so far; stop at the first failure."""
    report = Report()
    started = time.monotonic()
    exported: dict[str, str] = {}
    for block in blocks:
        if block.suite == ENV_SUITE:
            exported.update(parse_env(block))
            report.env = dict(exported)
            continue
        if block.suite == "manual":
            continue
        result = _run_block(block, {**env, **exported}, workdir)
        report.results.append(result)
        if result.error is not None:
            report.fail(block.line, f"README line {block.line}: {result.error}")
            break
    report.seconds = time.monotonic() - started
    return report


def _run_block(block: Block, env: Mapping[str, str], workdir: Path) -> BlockResult:
    _say(f"::group::readme:{block.suite}:{block.order} (line {block.line})")
    began = time.monotonic()
    # Its own session: on a timeout the whole process group goes (curl, sleep, python3
    # started by the block), not only bash, so nothing keeps running or holds the pipe.
    proc = subprocess.Popen(  # noqa: S603  # the README's own commands, by design
        ["bash", "-euo", "pipefail", "-c", block.code],  # noqa: S607  # bash from PATH, as a reader would
        cwd=workdir,
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, _ = proc.communicate(timeout=block.timeout)
    except subprocess.TimeoutExpired:
        output = _kill_group(proc)
        _say(output + "::endgroup::")
        return BlockResult(
            block.line,
            block.suite,
            block.order,
            None,
            time.monotonic() - began,
            output,
            f"timed out after {block.timeout} s",
        )
    done = subprocess.CompletedProcess(proc.args, proc.returncode, stdout)
    output = done.stdout
    _say(output + "::endgroup::")
    error = None
    if done.returncode != 0:
        error = f"exit {done.returncode}"
    elif block.expect is not None and not block.expect.search(output):
        error = f"output does not match expect={block.expect.pattern}"
    return BlockResult(
        block.line,
        block.suite,
        block.order,
        done.returncode,
        time.monotonic() - began,
        output,
        error,
    )


def _kill_group(proc: subprocess.Popen[str]) -> str:
    """SIGKILL the block's process group, reap bash and return the output so far. A
    process that left the group and still holds the pipe is not waited for long."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    try:
        output, _ = proc.communicate(timeout=KILL_GRACE_S)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.wait()
        return _text(exc.output)
    return _text(output)


def _text(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    return raw.decode(errors="replace") if isinstance(raw, bytes) else raw


# --- Health ---------------------------------------------------------------------------------


def wait_healthy(
    url: str, timeout_s: int = HEALTH_TIMEOUT_S, ca_file: Path | None = None
) -> dict[str, Any]:
    """Poll `url` (the app's /health/ready) until it answers 200, verifying TLS against
    `ca_file` when given (the README's own certificate); return the body. TimeoutError
    when it has not answered 200 within `timeout_s`."""
    context = ssl.create_default_context(cafile=str(ca_file) if ca_file else None)
    deadline = time.monotonic() + timeout_s
    last = "no answer yet"
    while True:
        try:
            with urllib.request.urlopen(url, timeout=10, context=context) as answer:  # noqa: S310  # the operator's own URL
                body: dict[str, Any] = json.loads(answer.read())
                return body
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}: {_text(exc.read())[:300]}"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = f"{type(exc).__name__}: {exc}"
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{url} not ready after {timeout_s} s ({last})")
        time.sleep(5)


def health_problems(body: Mapping[str, Any]) -> list[str]:
    """What a fresh install's readiness must not show: a critical check or a module check
    that is not `ok` (adapters and backups may be degraded before they are configured)."""
    checks: Mapping[str, str] = body.get("checks") or {}
    problems = [
        f"{name}: {checks.get(name)}" for name in CRITICAL_CHECKS if checks.get(name) != "ok"
    ]
    problems += [
        f"{name}: {status}"
        for name, status in sorted(checks.items())
        if name.startswith("module:") and status != "ok"
    ]
    return problems


# --- Command line ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--readme", type=Path, default=REPO / "README.md")
    parser.add_argument(
        "--suite", action="append", default=[], help="a suite to run; repeat for more"
    )
    parser.add_argument("--section", default="Install", help="the section every block is tagged in")
    parser.add_argument("--workdir", type=Path, default=REPO)
    parser.add_argument("--health-url", help="poll this /health/ready after the first suite")
    parser.add_argument("--ca-file", type=Path, help="trust this certificate for --health-url")
    parser.add_argument("--report", type=Path, help="write the JSON report here")
    parser.add_argument("--list", action="store_true", help="list the blocks; run nothing")
    args = parser.parse_args(argv)

    markdown = args.readme.read_text(encoding="utf-8")
    try:
        blocks = extract(markdown)
        untagged = check_section_coverage(markdown, args.section)
    except ValueError as exc:
        _say(f"readme_test: {args.readme.name}: {exc}")
        return 1
    if untagged:
        lines = ", ".join(str(line) for line in untagged)
        _say(f"readme_test: untagged shell blocks in {args.section!r}, lines {lines}")
        return 1
    if args.list:
        for block in blocks:
            print(f"{block.line:5}  readme:{block.suite}:{block.order}  timeout={block.timeout}")
        return 0

    report = Report()
    started = time.monotonic()
    env = dict(os.environ)
    for index, suite in enumerate(args.suite or ["install"]):
        chosen = select(blocks, suite)
        if not [block for block in chosen if block.suite == suite]:
            report.fail(None, f"suite {suite!r} has no blocks in {args.readme.name}")
            break
        part = run(chosen, env, args.workdir)
        report.results += part.results
        report.env.update(part.env)
        env.update(part.env)
        if not part.ok:
            report.fail(part.failed_line, part.failure)
            break
        if index == 0 and args.health_url:
            try:
                report.health = wait_healthy(args.health_url, ca_file=args.ca_file)
            except TimeoutError as exc:
                report.fail(None, str(exc))
                break
            problems = health_problems(report.health)
            if problems:
                report.fail(None, "not healthy: " + "; ".join(problems))
                break
    report.seconds = time.monotonic() - started
    _finish(report, args.report)
    return 0 if report.ok else 1


def _finish(report: Report, path: Path | None) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report.to_json() + "\n", encoding="utf-8")
    verdict = "ok" if report.ok else f"FAILED: {report.failure}"
    _say(f"readme_test: {len(report.results)} blocks in {report.seconds:.0f} s, {verdict}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write(f"### README harness\n\n{verdict}, {len(report.results)} blocks, ")
            out.write(f"{report.seconds / 60:.1f} minutes in total\n\n")
            out.write("| Line | Suite | Seconds | Result |\n| --- | --- | --- | --- |\n")
            for r in report.results:
                out.write(
                    f"| {r.line} | {r.suite}:{r.order} | {r.seconds:.0f} | {r.error or 'ok'} |\n"
                )


if __name__ == "__main__":
    sys.exit(main())
