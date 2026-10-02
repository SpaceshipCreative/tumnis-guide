"""The README harness, scripts/readme_test.py (P4-06, A4.4).

The README's install commands are fenced blocks tagged in the info string
(`bash readme:install:30 timeout=900`); the harness extracts them, checks that the Install
section has no untagged shell block, and runs each block in its own `bash -euo pipefail`
process, exporting only the `readme:env` blocks' KEY=value lines. The README job
(.github/workflows/readme.yml) runs it on a clean VM. The script sits outside the backend
package, so the tests load it by path inside their bodies: a missing script fails the test,
not collection.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[3]
HARNESS = REPO / "scripts" / "readme_test.py"
FIXTURE = Path(__file__).parent / "data" / "readme_fixture.md"


def _harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location("readme_test", HARNESS)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["readme_test"] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


def _base_env() -> dict[str, str]:
    """What a block needs from the parent environment: PATH (bash, coreutils) only."""
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}


@pytest.mark.req("A4.4", "REL-4")
@pytest.mark.wp("P4-06")
def test_extract_blocks_and_order() -> None:
    """T-P4-06-01
    The fixture gives every tagged block in document order with its suite, order, language,
    timeout (300 s unless given), `expect` pattern and the line of its opening fence;
    untagged blocks are left out; a suite runs with the env blocks in document order, and
    its orders must rise down the document; an unknown token, a malformed tag or an order
    out of sequence is refused with its line.
    """
    readme = _harness()
    blocks = readme.extract(FIXTURE.read_text())

    got = [(b.suite, b.order, b.lang, b.timeout, b.line) for b in blocks]
    assert got == [
        ("env", 0, "bash", 300, 6),
        ("install", 10, "bash", 300, 12),
        ("install", 20, "sh", 900, 16),
        ("install", 30, "bash", 60, 20),
        ("manual", 0, "bash", 300, 34),
        ("first-run", 10, "bash", 300, 40),
        ("upgrade", 10, "bash", 1200, 50),
    ]
    assert blocks[0].code == "TUMNIS_HOST=localhost\n"
    assert blocks[1].code == "docker --version\n"
    assert all(b.expect is None for b in blocks if b.line != 20)
    pattern = blocks[3].expect
    assert pattern is not None
    assert pattern.search("ready now")
    assert not pattern.search("already")

    # A suite's blocks with the env blocks, in document order (env exports reach only the
    # blocks after them); within a suite the orders must rise down the document.
    install = readme.select(blocks, "install")
    assert [(b.suite, b.order) for b in install] == [
        ("env", 0),
        ("install", 10),
        ("install", 20),
        ("install", 30),
    ]
    swapped = "```bash readme:install:20\necho b\n```\n\n```bash readme:install:10\necho a\n```\n"
    with pytest.raises(ValueError, match="line 5"):
        readme.extract(swapped)

    with pytest.raises(ValueError, match="line 3"):
        readme.extract("Text\n\n```bash readme:install:10 colour=blue\necho\n```\n")
    with pytest.raises(ValueError, match="line 1"):
        readme.extract("```bash readme:install:ten\necho\n```\n")
    with pytest.raises(ValueError, match="line 1"):
        readme.extract("```bash readme:install:10 timeout=soon\necho\n```\n")


@pytest.mark.req("A4.4", "REL-4")
@pytest.mark.wp("P4-06")
def test_untagged_block_in_install_fails() -> None:
    """T-P4-06-02
    The coverage check lists the line of every untagged shell block inside the Install
    section (sub-sections included, up to the next heading of the same level); untagged
    blocks in other languages or outside the section pass, and a README without an
    Install section fails the check instead of passing it silently.
    """
    readme = _harness()
    assert readme.check_section_coverage(FIXTURE.read_text()) == [28]
    assert readme.check_section_coverage(FIXTURE.read_text(), section="First run") == [44]

    tagged = "## Install\n\n```bash readme:install:10\necho ok\n```\n\n## Next\n\n```bash\nx\n```\n"
    assert readme.check_section_coverage(tagged) == []
    nested = "## Install\n\n### Step\n\n```console\n$ docker ps\n```\n"
    assert readme.check_section_coverage(nested) == [5]
    with pytest.raises(ValueError, match="Install"):
        readme.check_section_coverage("# Title\n\nNo install section here.\n")


@pytest.mark.req("A4.4", "REL-4")
@pytest.mark.wp("P4-06")
def test_block_failure_stops_run_with_line_number(tmp_path: Path) -> None:
    """T-P4-06-03
    Each block runs in its own `bash -euo pipefail` process in the work directory; the
    first block that exits non-zero, misses its `expect` pattern or runs out of time stops
    the run, the report names that block's README line, and no later block runs.
    """
    readme = _harness()
    markdown = (
        "## Install\n\n"
        "```bash readme:install:10\necho one > first.txt\n```\n\n"
        "```bash readme:install:20\nfalse\necho never > second.txt\n```\n\n"
        "```bash readme:install:30\necho three > third.txt\n```\n"
    )
    report = readme.run(readme.select(readme.extract(markdown), "install"), _base_env(), tmp_path)
    assert not report.ok
    assert report.failed_line == 7
    assert "line 7" in report.failure
    assert (tmp_path / "first.txt").read_text() == "one\n"
    assert not (tmp_path / "second.txt").exists()  # -e: the failing command stops the block
    assert not (tmp_path / "third.txt").exists()  # and the run
    assert [r.line for r in report.results] == [3, 7]
    assert report.results[1].exit_code != 0

    piped = "```bash readme:install:10\nfalse | cat\necho after > after.txt\n```\n"
    report = readme.run(readme.extract(piped), _base_env(), tmp_path)
    assert (report.ok, report.failed_line) == (False, 1)  # pipefail
    assert not (tmp_path / "after.txt").exists()

    unset = '```bash readme:install:10\necho "$NOT_DEFINED_ANYWHERE"\n```\n'
    assert readme.run(readme.extract(unset), _base_env(), tmp_path).failed_line == 1  # -u

    expect = "```bash readme:install:10 expect=^healthy$\necho degraded\n```\n"
    report = readme.run(readme.extract(expect), _base_env(), tmp_path)
    assert (report.ok, report.failed_line) == (False, 1)
    assert "expect" in report.failure

    slow = "```bash readme:install:10 timeout=1\nsleep 5\n```\n"
    report = readme.run(readme.extract(slow), _base_env(), tmp_path)
    assert (report.ok, report.failed_line) == (False, 1)
    assert "timed out" in report.failure

    good = "```bash readme:install:10 expect=^ok$\necho ok\n```\n"
    report = readme.run(readme.extract(good), _base_env(), tmp_path)
    assert report.ok
    assert report.failed_line is None
    assert report.results[0].output == "ok\n"


@pytest.mark.req("A4.4", "REL-4")
@pytest.mark.wp("P4-06")
def test_env_blocks_are_the_only_substitution(tmp_path: Path) -> None:
    """T-P4-06-04
    A `readme:env` block's KEY=value lines are exported, literally, to the blocks after it
    (and not before); the harness expands no template syntax in a block or a value, so
    `{{X}}` and `${X}` inside single quotes reach bash unchanged. A line that is not
    KEY=value is refused with its line.
    """
    readme = _harness()
    markdown = (
        "```bash readme:install:10\n"
        "printf '%s\\n' \"${TUMNIS_HOST-unset}\" > before.txt\n"
        "```\n\n"
        "```bash readme:env\n"
        "# a comment, then a blank line\n\n"
        "TUMNIS_HOST=localhost\n"
        "GREETING=hello {{name}}\n"
        "LITERAL=$HOME/x\n"
        "```\n\n"
        "```bash readme:install:20\n"
        "printf '%s|%s|%s|%s|%s\\n' \"$TUMNIS_HOST\" '{{TUMNIS_HOST}}' '${TUMNIS_HOST}' "
        '"$GREETING" "$LITERAL" > after.txt\n'
        "```\n"
    )
    blocks = readme.extract(markdown)
    report = readme.run(readme.select(blocks, "install"), _base_env(), tmp_path)
    assert report.ok, report.failure
    assert (tmp_path / "before.txt").read_text() == "unset\n"
    assert (tmp_path / "after.txt").read_text() == (
        "localhost|{{TUMNIS_HOST}}|${TUMNIS_HOST}|hello {{name}}|$HOME/x\n"
    )
    assert report.env == {
        "TUMNIS_HOST": "localhost",
        "GREETING": "hello {{name}}",
        "LITERAL": "$HOME/x",
    }

    with pytest.raises(ValueError, match="line 2"):
        readme.run(
            readme.extract("```bash readme:env\nexport TUMNIS_HOST localhost\n```\n"),
            _base_env(),
            tmp_path,
        )


def _alive(pid: int) -> bool:
    """Running (or stopped), not gone and not a zombie waiting to be reaped."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


@pytest.mark.req("A4.4", "REL-4")
@pytest.mark.wp("P4-06")
@pytest.mark.skipif(not Path("/proc").is_dir(), reason="reads /proc to see the child")
def test_a_timed_out_block_takes_its_children_with_it(tmp_path: Path) -> None:
    """A block that times out is killed with its whole process group: a child it started
    (here a background sleep) does not outlive the run (CodeRabbit, PR #159)."""
    readme = _harness()
    markdown = (
        "```bash readme:install:10 timeout=1\nsleep 60 &\necho $! > child.pid\nsleep 60\n```\n"
    )
    report = readme.run(readme.extract(markdown), _base_env(), tmp_path)
    assert not report.ok
    assert "timed out after 1 s" in (report.failure or "")
    child = int((tmp_path / "child.pid").read_text())
    deadline = time.monotonic() + 5
    while _alive(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(child)


@pytest.mark.req("A4.4", "REL-4")
@pytest.mark.wp("P4-06")
def test_section_ends_only_at_a_real_closing_fence() -> None:
    """Inside a four-backtick block, a three-backtick line or one with an info string
    does not close it, so a `# Heading` line inside the block is not a heading and the
    section runs on to the next real heading (CodeRabbit, PR #159)."""
    readme = _harness()
    markdown = (
        "## Install\n"  # 1
        "````markdown\n"  # 2
        "```bash\n"  # 3
        "# Upgrade\n"  # 4
        "```\n"  # 5
        "````\n"  # 6
        "```bash\n"  # 7
        "echo untagged\n"  # 8
        "```\n"  # 9
        "## Upgrade\n"  # 10
    )
    assert readme.section_lines(markdown, "Install") == range(1, 10)
    assert readme.check_section_coverage(markdown) == [7]
