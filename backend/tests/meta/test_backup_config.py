"""Backup configuration as code (P0-28, REL-1, SEC-9, ADR-0006): WAL archiving, the two
pgBackRest repositories and the never-sync rule for folder backups."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "deploy"
SCRIPTS = REPO / "scripts"
PGBACKREST_CONF = DEPLOY / "pgbackrest" / "pgbackrest.conf"
ARCHIVE_CONF = DEPLOY / "postgres" / "conf.d" / "archive.conf"
FOLDERS_BACKUP = DEPLOY / "rclone" / "folders-backup.sh"

# rclone subcommands that delete at the destination (or at the source, for move).
_DESTRUCTIVE_RCLONE = re.compile(r"\brclone\b[^\n]*\s(sync|bisync|move|purge|delete|deletefile)\b")
_SECRET_OPTIONS = ("repo2-s3-key", "repo2-s3-key-secret", "repo2-cipher-pass", "repo1-cipher-pass")


def _code_lines(path: Path) -> list[str]:
    """The file's lines that are not comments (shell, ini, YAML all comment with #)."""
    return [line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")]


def _conf(path: Path) -> dict[str, dict[str, str]]:
    """A pgBackRest/Postgres style `key = value` file by section ("" before any section)."""
    sections: dict[str, dict[str, str]] = {"": {}}
    current = ""
    for raw in _code_lines(path):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, {})
            continue
        key, sep, value = line.partition("=")
        assert sep, f"{path.name}: not a key=value line: {raw!r}"
        sections[current][key.strip()] = value.strip()
    return sections


@pytest.mark.contract
@pytest.mark.req("REL-1")
@pytest.mark.wp("P0-28")
def test_no_rclone_sync_anywhere() -> None:
    """T-P0-28-05
    No script or deploy file runs `rclone sync` (or any rclone command that deletes at the
    destination): a folder backup only ever copies, so a file removed at the source stays in
    the backup. The folder backup script copies with checksums.
    """
    files = [
        p
        for root in (SCRIPTS, DEPLOY, REPO / ".github")
        for p in root.rglob("*")
        if p.is_file() and p.suffix in {"", ".sh", ".yml", ".yaml", ".conf", ".py", ".env"}
    ]
    assert files
    offenders = [
        f"{p.relative_to(REPO)}: {line.strip()}"
        for p in files
        for line in _code_lines(p)
        if _DESTRUCTIVE_RCLONE.search(line)
    ]
    assert offenders == []

    script = "\n".join(_code_lines(FOLDERS_BACKUP))
    assert re.search(r"\brclone\s+copy\b", script)
    assert "--checksum" in script


@pytest.mark.contract
@pytest.mark.req("REL-1", "ADR-0006")
@pytest.mark.wp("P0-28")
def test_archive_settings() -> None:
    """T-P0-28-06
    Postgres archives every WAL segment through pgBackRest and forces a segment switch at
    least once a minute (archive_timeout = 60s), so a quiet database still ships WAL well
    inside the 15-minute RPO. postgresql.conf includes conf.d and keeps no placeholder.
    """
    archive = _conf(ARCHIVE_CONF)[""]
    assert archive["wal_level"] == "replica"
    assert archive["archive_mode"] == "on"
    assert archive["archive_timeout"] == "60s"
    assert archive["archive_command"] == "'pgbackrest --stanza=tumnis archive-push %p'"

    main = _conf(DEPLOY / "postgres" / "postgresql.conf")[""]
    assert "archive_command" not in main
    assert main.get("include_dir") == "'conf.d'"
    dockerfile = (DEPLOY / "postgres" / "Dockerfile").read_text()
    assert re.search(r"^COPY\s+conf\.d/?\s+/etc/postgresql/conf\.d/?\s*$", dockerfile, re.M)


@pytest.mark.contract
@pytest.mark.req("SEC-9")
@pytest.mark.wp("P0-28")
def test_pgbackrest_repos() -> None:
    """T-P0-28-07
    Two repositories: repo1 local with a retention, repo2 S3 (B2) encrypted with no
    retention (pgBackRest never expires, so it never needs to delete, in repo2), and no
    secret in the committed file (they live in conf.d/secrets.conf on the host).
    """
    conf = _conf(PGBACKREST_CONF)
    glob = conf["global"]
    assert glob["repo1-path"] == "/var/lib/pgbackrest"
    assert int(glob["repo1-retention-full"]) >= 1
    assert glob["repo2-type"] == "s3"
    assert glob["repo2-cipher-type"] == "aes-256-cbc"
    assert glob["repo2-s3-bucket"]
    assert glob["repo2-s3-endpoint"]
    assert not [key for key in glob if key.startswith("repo2-retention")]
    assert conf["tumnis"]["pg1-path"]

    for section in conf.values():
        for key in _SECRET_OPTIONS:
            assert key not in section, key
    text = PGBACKREST_CONF.read_text()
    for key in _SECRET_OPTIONS:
        assert not re.search(rf"^\s*{re.escape(key)}\s*=", text, re.M), key
