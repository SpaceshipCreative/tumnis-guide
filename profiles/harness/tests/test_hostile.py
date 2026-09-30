"""The hostile suite's plumbing (P2-11, SAF-6): the generated index, packet injection,
the PR subset, the mock-only profile copies, transcripts and the command line. None of
these run a model; the suite itself runs on the homelab runner (T-P2-11-01)."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

pytestmark = [pytest.mark.req("SAF-6"), pytest.mark.wp("P2-11")]


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def test_committed_index_is_current() -> None:
    """The committed Skills-job index is exactly what `index --suite hostile` writes."""
    from harness.hostile import INDEX, discover_skills, load_hostile, render_index

    assert INDEX.read_text(encoding="utf-8") == render_index(load_hostile(), discover_skills())


def test_render_prompt_keeps_the_recorded_prompt() -> None:
    """Re-rendering a base packet's unchanged body gives back its recorded prompt, so an
    injected packet differs from the recording only by what was injected."""
    from harness.hostile import BASES, render_prompt

    for base in BASES.values():
        packet = json.loads(base.packet.read_text(encoding="utf-8"))
        assert render_prompt(packet["prompt_text"], packet["body"]) == packet["prompt_text"]


def test_every_run_packet_validates_and_carries_its_payload() -> None:
    """Every (case or twin, skill) packet is a request its skill can read, and holds every
    part of the payload, invisible characters included, byte for byte."""
    from harness.assertions import RULE_REQUESTS
    from harness.hostile import discover_skills, expand, load_hostile

    hostile = load_hostile()
    runs = expand(hostile, discover_skills())
    assert {run.kind for run in runs} == {"hostile", "twin"}
    for run in runs:
        packet = run.harness_case.packet
        for rule in run.base.rules:
            RULE_REQUESTS[rule].model_validate(packet["body"])
        item = run.case if run.kind == "hostile" else hostile.twins[run.case.benign_twin]
        assert run.item_id == item.id
        texts = _strings(packet["body"])
        for part in (item, *item.companions):
            assert any(part.content.strip() in t for t in texts), run.label
        assert "<packet>\n" in packet["prompt_text"]


def test_smoke_picks_smoke_changed_skills_and_changed_cases() -> None:
    """The PR subset: the index's smoke cases, plus every case of a changed skill, plus a
    changed case; the nightly run takes everything."""
    from harness.hostile import BASES, discover_skills, expand, load_hostile

    hostile = load_hostile()
    skills = discover_skills()
    per_case = sum(1 for s in skills if s.skill in BASES)
    full = expand(hostile, skills)
    assert len(full) == len(hostile.cases) * per_case * 2

    smoke = expand(hostile, skills, smoke=True)
    assert {run.case.id for run in smoke} == set(hostile.smoke)
    assert len(smoke) == len(hostile.smoke) * per_case * 2

    extra = next(cid for cid in hostile.cases if cid not in hostile.smoke)
    with_case = expand(hostile, skills, smoke=True, changed_cases={extra})
    assert {run.case.id for run in with_case} == {*hostile.smoke, extra}

    with_skill = expand(hostile, skills, smoke=True, changed_skills={"enrich"})
    enrich = {run.case.id for run in with_skill if run.base.skill == "enrich"}
    plan = {run.case.id for run in with_skill if run.base.skill == "plan"}
    assert enrich == set(hostile.cases)
    assert plan == set(hostile.smoke)


def test_changed_maps_paths_to_skills_and_cases() -> None:
    """A skill file changes that skill; another profile file changes all of its skills; a
    case or twin file changes its case; anything else changes nothing."""
    from harness import REPO
    from harness.hostile import changed, discover_skills, load_hostile

    hostile = load_hostile()
    skills = discover_skills()
    case = next(iter(hostile.cases.values()))
    twin = hostile.twins[case.benign_twin]
    assert case.path is not None
    assert twin.path is not None
    master = {s.skill for s in skills if s.profile == "master"}

    assert changed(["profiles/master/skills/plan/SKILL.md"], hostile, skills) == ({"plan"}, set())
    assert changed(["profiles/master/SOUL.md"], hostile, skills) == (master, set())
    for item_path in (case.path, twin.path):
        path = item_path.resolve().relative_to(REPO).as_posix()
        assert changed([path], hostile, skills) == (set(), {case.id})
    assert changed(["backend/tumnis/app.py", "README.md"], hostile, skills) == (set(), set())


@pytest.mark.parametrize("profile", ["master", "project-template"])
def test_prepared_profile_runs_only_the_mocks(tmp_path: Path, profile: str) -> None:
    """A hostile run's profile copy replaces every MCP server (Jev included) with a
    recording mock started by this interpreter, all writing to the slot's record file."""
    from harness.hostile_run import prepare_profile
    from harness.mock_worker_tools import WORKER_TOOLS

    record = tmp_path / "h0" / "calls.jsonl"
    dest = prepare_profile(profile, tmp_path / "h0", record)
    assert (dest / "SOUL.md").is_file()
    text = (dest / "mcp.json").read_text(encoding="utf-8")
    servers = json.loads(text)["mcpServers"]
    assert set(servers) == {"tumnis", *WORKER_TOOLS}
    assert "jev" in servers
    for name, server in servers.items():
        assert server["command"] == sys.executable, name
        module = "harness.mock_mcp_min" if name == "tumnis" else "harness.mock_worker_tools"
        assert server["args"][:2] == ["-m", module]
        assert str(record) in server["args"]
    assert "uvx" not in text
    assert "http" not in text
    assert "TYPESAFE_API_KEY" not in text


def test_transcript_keeps_built_in_calls_as_hermes() -> None:
    """The judge sees the mocks' calls, then Hermes' own tool calls (not `mcp_*`) under
    the server `hermes`, and the reply."""
    from harness.cases import SchemaName
    from harness.hostile_run import HERMES, transcript_of
    from harness.mock_mcp_min import RecordedCall
    from harness.run import Attempt, ToolCall

    schema = SchemaName("enrichment", "result", 1)
    records = [RecordedCall("tumnis", "create_task", {"title": "x"}, {"ok": True})]
    attempt = Attempt(
        outcome="pass",
        tool_calls=(
            ToolCall("mcp_tumnis_create_task", {"title": "x"}),
            ToolCall("terminal", {"command": "ls"}),
        ),
        output={"task_id": "t"},
    )
    transcript = transcript_of(attempt, records, schema)
    assert transcript.calls == (
        records[0],
        RecordedCall(HERMES, "terminal", {"command": "ls"}),
    )
    assert transcript.output == {"task_id": "t"}
    assert transcript.output_schema == schema


def test_run_suite_gives_every_run_its_verdicts() -> None:
    """Every run gets `runs_each` verdicts in order, and fewer than 3 is refused."""
    from harness.hostile import discover_skills, expand, load_hostile
    from harness.hostile_run import run_suite, summary
    from harness.judge import Verdict
    from harness.run import HarnessError

    runs = expand(load_hostile(), discover_skills(), smoke=True)[:4]
    calls: list[tuple[str, int]] = []

    def stub(run: object, number: int) -> Verdict:
        label = run.label  # type: ignore[attr-defined]
        calls.append((label, number))
        failed = label == runs[0].label and number == 2
        return Verdict(False, ("forbidden call",)) if failed else Verdict(True)

    results = run_suite(runs, stub, runs_each=3, parallel=2)
    assert [r.label for r in results] == [run.label for run in runs]
    assert all(len(r.verdicts) == 3 for r in results)
    assert sorted(calls) == sorted((run.label, n) for run in runs for n in (1, 2, 3))
    assert not results[0].verdict.passed
    assert all(r.verdict.passed for r in results[1:])
    assert "failed (2/3)" in summary(results[0])
    assert "run 2: forbidden call" in summary(results[0])

    with pytest.raises(HarnessError):
        run_suite(runs, stub, runs_each=2)


def test_cli_coverage_index_and_refusals(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`coverage` and `index --check` pass on the committed set; `run --suite hostile`
    refuses fewer than 3 runs and an unpinned model before running anything."""
    import harness.__main__ as cli
    from harness.run import load_config

    assert cli.main(["coverage", "--suite", "hostile"]) == 0
    assert "0 gaps" in capsys.readouterr().out
    assert cli.main(["index", "--suite", "hostile", "--check"]) == 0
    assert "current" in capsys.readouterr().out

    assert cli.main(["run", "--suite", "hostile", "--runs", "2"]) == 2
    assert "every case runs 3 times" in capsys.readouterr().err

    unset = replace(load_config(), model="UNSET-pinned-homelab-model")
    monkeypatch.setattr(cli, "load_config", lambda: unset)
    assert cli.main(["run", "--suite", "hostile"]) == 2
    assert "pin the homelab model" in capsys.readouterr().err
