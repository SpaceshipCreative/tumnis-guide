"""Static checks on the profiles (P2-12): no model runs here.

- The digest skills run on a Hermes cron: a profile distribution ships its cron jobs as
  `cron/jobs.json` (every other file under `cron/` is runtime state the installer leaves
  out; hermes_cli/profile_distribution.py), hourly inside the default working hours
  (FR-4.7: Monday to Friday, 09:00 to 18:00).
- The template's MCP servers are pre-configured in `config.yaml` under `mcp_servers`
  (where Hermes reads them), credentials only as `${VAR}` placeholders filled from the
  profile's own `.env`; `.env.example` holds placeholders only.
- Worker routing is the profile's job (FR-5.3): no Tumnis code path names a worker.
- A change under a profile needs a VERSION bump (scripts/ci/profile_version_check.py).
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from harness import REPO

TEMPLATE = REPO / "profiles" / "project-template"
MASTER = REPO / "profiles" / "master"
PLACEHOLDER = re.compile(r"\$\{[A-Z][A-Z0-9_]*\}")
# Shapes of real credentials: GitHub classic and fine-grained tokens, Tumnis keys, task and
# device tokens, OpenAI-style keys, bearer strings with a literal value.
SECRET_SHAPES = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\btm[ntd]_[A-Za-z0-9]{8,}_[A-Za-z0-9]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer\s+(?!\$\{)[A-Za-z0-9._-]{12,}"),
)
TEMPLATE_SERVERS = {"tumnis", "jev", "github", "coolify"}  # projects.rules TOOL_ALLOWLIST_DEFAULT
PLACEHOLDERS = {
    "TUMNIS_URL",
    "TUMNIS_TOKEN",
    "GITHUB_PERSONAL_ACCESS_TOKEN",
    "COOLIFY_BASE_URL",
    "COOLIFY_TOKEN",
    "TYPESAFE_API_KEY",
}


def _jobs(profile: Path) -> list[dict[str, Any]]:
    data = json.loads((profile / "cron" / "jobs.json").read_text(encoding="utf-8"))
    jobs = data["jobs"]
    assert isinstance(jobs, list)
    return jobs


def _hourly_in_working_hours(expr: str) -> bool:
    """A five-field cron expression firing once an hour, on the hour, from 09:00 to 17:00,
    Monday to Friday: every run starts inside the default working hours."""
    fields = expr.split()
    if len(fields) != 5:
        return False
    minute, hour, day, month, weekday = fields
    return (minute, hour, day, month, weekday) == ("0", "9-17", "*", "*", "1-5")


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


@pytest.mark.req("FR-13.2")
@pytest.mark.wp("P2-12")
def test_digest_cron_hourly_in_working_hours() -> None:
    """T-P2-12-06
    The template ships `cron/jobs.json` with one job running the `project-digest` skill
    hourly inside working hours (`0 9-17 * * 1-5`), and the master ships one running
    `workspace-digest` on the same schedule; each job has a stable id and a name, and no
    other file sits under either profile's `cron/` (the installer would drop it).
    """
    for profile, skill in ((TEMPLATE, "project-digest"), (MASTER, "workspace-digest")):
        jobs = _jobs(profile)
        digest = [job for job in jobs if job.get("skills") == [skill]]
        assert len(digest) == 1, (profile.name, jobs)
        job = digest[0]
        assert re.fullmatch(r"[a-z0-9-]{3,64}", str(job["id"]))
        assert str(job["name"]).strip()
        assert str(job["prompt"]).strip()
        assert _hourly_in_working_hours(str(job["schedule"])), job["schedule"]
        assert sorted(p.name for p in (profile / "cron").iterdir()) == ["jobs.json"]
        assert (profile / "skills" / skill / "SKILL.md").is_file()


@pytest.mark.req("FR-5.3", "FR-11.6")
@pytest.mark.wp("P2-12")
def test_mcp_servers_preconfigured() -> None:
    """T-P2-12-08
    The template's config.yaml pre-configures `tumnis` (HTTP, bearer `${TUMNIS_TOKEN}`),
    `jev` (the same pinned server as mcp.json), `github` (the GitHub MCP server, pinned by
    version, token from `${GITHUB_PERSONAL_ACCESS_TOKEN}`) and `coolify` (Coolify's own MCP
    endpoint under `${COOLIFY_BASE_URL}`, bearer `${COOLIFY_TOKEN}`): exactly the template
    tool allow-list. The master carries `tumnis` and `jev`. Every credential is a `${VAR}`
    placeholder; no file under either profile holds a token; `.env.example` names each
    placeholder with an empty value, and no `.env` is in the repo.
    """
    config = yaml.safe_load((TEMPLATE / "config.yaml").read_text(encoding="utf-8"))
    servers = config["mcp_servers"]
    assert set(servers) == TEMPLATE_SERVERS

    tumnis = servers["tumnis"]
    assert tumnis["url"] == "${TUMNIS_URL}/mcp"
    assert tumnis["headers"] == {"Authorization": "Bearer ${TUMNIS_TOKEN}"}

    jev_json = json.loads((TEMPLATE / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert servers["jev"] == jev_json["jev"]

    github = servers["github"]
    image = [arg for arg in github["args"] if arg.startswith("ghcr.io/github/github-mcp-server")]
    assert len(image) == 1
    assert re.fullmatch(r"ghcr\.io/github/github-mcp-server:v\d+\.\d+\.\d+", image[0])
    assert github["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "${GITHUB_PERSONAL_ACCESS_TOKEN}"  # noqa: S105

    coolify = servers["coolify"]
    assert coolify["url"] == "${COOLIFY_BASE_URL}/mcp"
    assert coolify["headers"] == {"Authorization": "Bearer ${COOLIFY_TOKEN}"}

    master = yaml.safe_load((MASTER / "config.yaml").read_text(encoding="utf-8"))
    assert set(master["mcp_servers"]) == {"tumnis", "jev"}
    assert master["mcp_servers"]["tumnis"] == tumnis

    for value in _strings(servers) + _strings(master["mcp_servers"]):
        for name in re.findall(r"\$\{([^}]*)\}", value):
            assert name in PLACEHOLDERS, name

    for profile in (TEMPLATE, MASTER):
        assert not (profile / ".env").exists()
        for path in profile.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace")
                assert not [s.pattern for s in SECRET_SHAPES if s.search(text)], path

    lines = [
        line.strip()
        for line in (TEMPLATE / ".env.example").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    names = {line.split("=", 1)[0] for line in lines}
    assert names == PLACEHOLDERS
    assert all(line.endswith("=") for line in lines), lines
    manifest = yaml.safe_load((TEMPLATE / "distribution.yaml").read_text(encoding="utf-8"))
    assert {entry["name"] for entry in manifest["env_requires"]} == PLACEHOLDERS


# Worker names a Tumnis code path must never carry (FR-5.3): routing lives in the profile.
WORKER_NAMES = re.compile(r"claude[ _-]?code|\bcodex\b", re.IGNORECASE)
TUMNIS_CODE = (
    (REPO / "backend" / "tumnis", ("*.py",)),
    (REPO / "daemon" / "tumnis_daemon", ("*.py",)),
    (REPO / "frontend" / "src", ("*.ts", "*.tsx")),
)
# Test files and fixtures are test data, not code paths: a fixture may mock an agent's own
# tool inventory (P2-17's AgentRail.test.tsx lists the worker tools an agent reports).
TEST_DIRS = {"tests", "test"}
TEST_FILE = re.compile(r"(\.test\.tsx?|_test\.py)$|^test_.*\.py$")


def _is_test_path(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    return bool(TEST_DIRS.intersection(rel.parts[:-1])) or bool(TEST_FILE.search(rel.name))


def _worker_names_in(code: tuple[tuple[Path, tuple[str, ...]], ...], base: Path) -> list[str]:
    return [
        str(path.relative_to(base))
        for root, globs in code
        for glob in globs
        for path in root.rglob(glob)
        if not _is_test_path(path, root)
        and WORKER_NAMES.search(path.read_text(encoding="utf-8", errors="replace"))
    ]


@pytest.mark.req("FR-5.3")
@pytest.mark.wp("P2-12")
def test_no_worker_routing_in_tumnis(tmp_path: Path) -> None:
    """T-P2-12-09
    Worker routing lives in the profile (FR-5.3): no source file of the backend, the
    daemon or the frontend names Claude Code or Codex, so Tumnis never routes to a worker.
    Test files and fixtures are not code paths and are not scanned.
    The check itself finds a planted name.
    """
    found = _worker_names_in(TUMNIS_CODE, REPO)
    assert found == []
    planted = tmp_path / "src" / "lib"
    planted.mkdir(parents=True)
    (planted / "route.ts").write_text("const worker = 'claude-code';\n", encoding="utf-8")
    (planted / "route.test.ts").write_text("const worker = 'codex';\n", encoding="utf-8")
    (tmp_path / "src" / "tests").mkdir()
    (tmp_path / "src" / "tests" / "fixture.ts").write_text("'codex'\n", encoding="utf-8")
    planted_code = ((tmp_path / "src", ("*.ts",)),)
    assert _worker_names_in(planted_code, tmp_path) == ["src/lib/route.ts"]
    assert WORKER_NAMES.search("route = 'claude-code'")
    assert WORKER_NAMES.search("if worker == 'Codex':")
    assert not WORKER_NAMES.search("encode codecs")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603  # fixed argv, no shell
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.mark.req("Quality rule 3")
@pytest.mark.wp("P2-12")
def test_version_bumped_on_change(tmp_path: Path) -> None:
    """T-P2-12-12
    A diff that changes a file under a profile (a skill, the SOUL, the cron jobs) without a
    higher VERSION fails the profile version check; the same diff with the bump passes.
    """
    spec = importlib.util.spec_from_file_location(
        "profile_version_check", REPO / "scripts" / "ci" / "profile_version_check.py"
    )
    assert spec is not None
    assert spec.loader is not None
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)

    repo = tmp_path / "repo"
    profile = repo / "profiles" / "project-template"
    (profile / "skills" / "orchestrate").mkdir(parents=True)
    (profile / "VERSION").write_text("1.1.0\n")
    (profile / "skills" / "orchestrate" / "SKILL.md").write_text("---\nname: orchestrate\n---\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD").strip()

    for changed in ("skills/orchestrate/SKILL.md", "SOUL.md", "cron/jobs.json"):
        target = profile / changed
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"changed {changed}\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"change {changed}")
        assert check.problems(repo, base, "HEAD") == [
            "profiles/project-template/ changed but profiles/project-template/VERSION is still"
            " 1.1.0"
        ]

    (profile / "VERSION").write_text("1.2.0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "bump")
    assert check.problems(repo, base, "HEAD") == []


# The Hermes Discord gateway's settings (hermes-agent docs, user-guide/messaging/discord):
# the bot token, the people it answers, and the channel ids, all from the profile's .env.
DISCORD_ENV = {
    "DISCORD_BOT_TOKEN",
    "DISCORD_ALLOWED_USERS",
    "DISCORD_ALLOWED_CHANNELS",
    "DISCORD_HOME_CHANNEL",
}
# Settings that name channels: the profile names its one channel in .env only.
CHANNEL_LISTS = {"allowed_channels", "free_response_channels", "ignored_channels"}


def _env_example(profile: Path) -> dict[str, str]:
    lines = [
        line.strip()
        for line in (profile / ".env.example").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    names = [line.split("=", 1)[0] for line in lines]
    assert len(names) == len(set(names)), names
    return dict(line.split("=", 1) for line in lines)


@pytest.mark.req("FR-8.2")
@pytest.mark.wp("P2-16")
@pytest.mark.xfail(strict=True, reason="spec:P2-16")
def test_only_master_has_discord_gateway() -> None:
    """T-P2-16-06
    Only the master talks to Discord (FR-8.2): the template's config has no messaging
    platform and no `discord` section, and its .env.example and manifest name no Discord
    variable. The master enables exactly one platform, Discord, answering every message in
    its channel inline (no mention, no threads), and names no channel in config.yaml: its
    .env.example has the bot token, the allowed users and the one channel, which is both
    the only channel it listens in and its home channel, each once and empty; the manifest
    requires each of them.
    """
    template = yaml.safe_load((TEMPLATE / "config.yaml").read_text(encoding="utf-8"))
    assert "platforms" not in template
    assert "discord" not in template
    assert not [n for n in _env_example(TEMPLATE) if n.startswith("DISCORD_")]
    manifest = yaml.safe_load((TEMPLATE / "distribution.yaml").read_text(encoding="utf-8"))
    assert not [e for e in manifest["env_requires"] if str(e["name"]).startswith("DISCORD_")]

    master = yaml.safe_load((MASTER / "config.yaml").read_text(encoding="utf-8"))
    assert master["platforms"] == {"discord": {"enabled": True}}
    discord = master["discord"]
    assert discord["require_mention"] is False
    assert discord["auto_thread"] is False
    assert not CHANNEL_LISTS & set(discord), discord

    env = _env_example(MASTER)
    found = {name for name in env if name.startswith("DISCORD_")}
    assert found == DISCORD_ENV
    assert all(env[name] == "" for name in DISCORD_ENV)
    channels = {n for n in found if "CHANNEL" in n}
    assert channels == {"DISCORD_ALLOWED_CHANNELS", "DISCORD_HOME_CHANNEL"}
    manifest = yaml.safe_load((MASTER / "distribution.yaml").read_text(encoding="utf-8"))
    required = {str(e["name"]) for e in manifest["env_requires"] if e.get("required")}
    assert required >= DISCORD_ENV
