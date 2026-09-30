"""The profile health probes (P2-10, SAF-2, SAF-3, FR-5.12): run on the host, as the user
that owns the profile, so the profile's tokens never leave it.

- MCP servers: the profile's `config.yaml` (key `mcp_servers`, Hermes's own config) and
  `mcp.json` (key `mcpServers`, what a profile distribution ships), reported as name,
  transport (`stdio` for a command, `http` for a URL) and a redacted target: the command's
  name without its path, or the URL's host. Arguments, env, headers, URL credentials and
  queries are never read into the report.
- GitHub: the token from the profile's `.env` (`GITHUB_TOKEN` or
  `GITHUB_PERSONAL_ACCESS_TOKEN`); `GET https://api.github.com/repos/{owner}/{repo}` for
  each own and foreign repo. Any token may read a public repo, so a repo counts as
  reachable only when `permissions.push` or `permissions.admin` is true.
- Coolify: the token and base URL from `.env` (`COOLIFY_TOKEN` or `COOLIFY_API_TOKEN`, and
  `COOLIFY_BASE_URL`); `GET {base}/api/v1/applications/{uuid}` for each own and foreign
  app; 200 is reachable. A token only ever goes to the Coolify its own profile names
  (Scott, decision 18): without `COOLIFY_BASE_URL` the probe reports "not configured" and
  sends nothing, and the server's Coolify URL is only compared with it, never used.

Each probe has a 5-second timeout and looks at no more than 50 targets per kind (plan
defaults). Answers go back as booleans and target names only; no error text from a
provider or a library (which could echo the token) is ever passed on.
"""

import asyncio
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePath
from typing import Any, Final
from urllib.parse import urlsplit

import httpx
import yaml

from tumnis_daemon.protocol import MAX_REACH_TARGETS, McpServerInfo, TokenReach

PROBE_TIMEOUT_S: Final = 5.0  # each request (plan default)
PROBE_CONCURRENCY: Final = 8
MAX_SERVERS: Final = 200
GITHUB_API: Final = "https://api.github.com"
GITHUB_API_VERSION: Final = "2022-11-28"  # as P2-13's GitHub adapter
GITHUB_TOKEN_VARS: Final = ("GITHUB_TOKEN", "GITHUB_PERSONAL_ACCESS_TOKEN")
COOLIFY_TOKEN_VARS: Final = ("COOLIFY_TOKEN", "COOLIFY_API_TOKEN")
COOLIFY_URL_VARS: Final = ("COOLIFY_BASE_URL",)

_SERVER_NAME: Final = re.compile(r"[^A-Za-z0-9_.-]")
_REPO: Final = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}")
_APP: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_ENV_LINE: Final = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def profile_dir(hermes_home: Path, profile: str) -> Path:
    """Where Hermes keeps a profile (its config, `.env`, `VERSION`)."""
    return hermes_home / "profiles" / profile


# --- MCP servers -------------------------------------------------------------------------


def _server(name: object, entry: object) -> McpServerInfo:
    safe = _SERVER_NAME.sub("_", str(name))[:128] or "_"
    spec: Mapping[str, Any] = entry if isinstance(entry, Mapping) else {}
    url, command = spec.get("url"), spec.get("command")
    if isinstance(url, str) and url.strip():
        try:
            host = urlsplit(url.strip()).hostname
        except ValueError:
            host = None
        return McpServerInfo(name=safe, transport="http", target=host[:253] if host else None)
    target = None
    if isinstance(command, str) and command.split():
        target = PurePath(command.split()[0]).name[:253] or None
    return McpServerInfo(name=safe, transport="stdio", target=target)


def _servers_in(data: object, key: str) -> Mapping[Any, Any]:
    found = data.get(key) if isinstance(data, Mapping) else None
    return found if isinstance(found, Mapping) else {}


def _load(path: Path, parse: Any) -> object:
    try:
        return parse(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return None


def mcp_servers(profile: Path) -> list[McpServerInfo]:
    """The profile's MCP servers by name: `config.yaml`'s `mcp_servers`, then `mcp.json`'s
    `mcpServers` for names the config does not have. A file that is missing or does not
    parse adds nothing."""
    found: dict[str, McpServerInfo] = {}
    sources = (
        (_load(profile / "config.yaml", yaml.safe_load), "mcp_servers"),
        (_load(profile / "mcp.json", json.loads), "mcpServers"),
    )
    for data, key in sources:
        for name, entry in _servers_in(data, key).items():
            server = _server(name, entry)
            found.setdefault(server.name, server)
    return sorted(found.values(), key=lambda s: s.name)[:MAX_SERVERS]


def read_env(profile: Path) -> dict[str, str]:
    """The profile's `.env` as a mapping (`KEY=value`, optionally quoted or exported);
    empty when there is none."""
    try:
        text = (profile / ".env").read_text(encoding="utf-8")
    except OSError:
        return {}
    env: dict[str, str] = {}
    for line in text.splitlines():
        found = _ENV_LINE.match(line.strip())
        if found is None:
            continue
        value = found.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":  # noqa: PLR2004
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        env[found.group(1)] = value
    return env


def _first(env: Mapping[str, str], names: Sequence[str]) -> str | None:
    return next((env[name] for name in names if env.get(name)), None)


def profile_version(profile: Path) -> str | None:
    """The profile's `VERSION` stamp, else its `distribution.yaml` version."""
    try:
        stamp = (profile / "VERSION").read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        stamp = []
    if stamp and stamp[0].strip():
        return stamp[0].strip()[:64]
    manifest = _load(profile / "distribution.yaml", yaml.safe_load)
    version = manifest.get("version") if isinstance(manifest, Mapping) else None
    return str(version)[:64] if version is not None else None


# --- Token reach -------------------------------------------------------------------------

Answer = tuple[bool | None, str | None]  # reachable (None: unknown), a safe error


async def _gathered(targets: Sequence[str], one: Any) -> list[Answer]:
    limit = asyncio.Semaphore(PROBE_CONCURRENCY)

    async def guarded(target: str) -> Answer:
        async with limit:
            answer: Answer = await one(target)
            return answer

    return list(await asyncio.gather(*(guarded(t) for t in targets)))


def _reach(
    own: Sequence[str], foreign: Sequence[str], answers: Sequence[Answer]
) -> tuple[dict[str, bool], list[str], list[str]]:
    own_answers, foreign_answers = answers[: len(own)], answers[len(own) :]
    own_reachable = {t: ok is True for t, (ok, _) in zip(own, own_answers, strict=True)}
    foreign_reachable = [t for t, (ok, _) in zip(foreign, foreign_answers, strict=True) if ok]
    errors = list(dict.fromkeys(err for _, err in answers if err))[:100]
    return own_reachable, foreign_reachable, errors


def _targets(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))[:MAX_REACH_TARGETS]


def _github_answer(repo: str, answer: httpx.Response) -> Answer:
    """Push or admin permission is reach; read access alone (any token has it on a public
    repo), 403 and 404 are not."""
    if answer.status_code == httpx.codes.OK:
        try:
            permissions = answer.json().get("permissions") or {}
        except (ValueError, AttributeError):
            return None, f"github {repo}: unreadable answer"
        return bool(permissions.get("push") or permissions.get("admin")), None
    if answer.status_code == httpx.codes.UNAUTHORIZED:
        return False, "github: token rejected (401)"
    if answer.status_code in {httpx.codes.FORBIDDEN, httpx.codes.NOT_FOUND}:
        return False, None
    return None, f"github {repo}: HTTP {answer.status_code}"


async def probe_github(
    client: httpx.AsyncClient, token: str | None, *, own: Sequence[str], foreign: Sequence[str]
) -> TokenReach:
    """Each repo's reach for the token: push or admin permission counts; 404, 403, or 200
    with read access only does not."""
    if not token:
        return TokenReach(token_present=False)
    own, foreign = _targets(own), _targets(foreign)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "tumnis-daemon",
    }

    async def one(repo: str) -> Answer:
        if not _REPO.fullmatch(repo):
            return False, f"github {repo[:100]}: not an owner/name"
        try:
            answer = await client.get(
                f"{GITHUB_API}/repos/{repo}", headers=headers, timeout=PROBE_TIMEOUT_S
            )
        except httpx.TimeoutException:
            return None, f"github {repo}: timed out"
        except httpx.HTTPError:  # its text may carry the request; never pass it on
            return None, f"github {repo}: request failed"
        return _github_answer(repo, answer)

    reachable, foreign_hits, errors = _reach(own, foreign, await _gathered([*own, *foreign], one))
    return TokenReach(
        token_present=True,
        own_reachable=reachable,
        foreign_reachable=foreign_hits,
        errors=errors,
    )


def _origin(url: str) -> tuple[str, str, int | None] | None:
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in {"https", "http"} or not parts.hostname:
        return None
    return parts.scheme, parts.hostname.lower(), port


async def probe_coolify(
    client: httpx.AsyncClient,
    base_url: str | None,
    token: str | None,
    *,
    own: Sequence[str],
    foreign: Sequence[str],
    server_base_url: str | None = None,
) -> TokenReach:
    """Each application's reach for the token at the profile's own Coolify (`base_url`,
    from its `.env`): 200 is reachable, 401, 403 and 404 are not. Nothing is sent when
    that URL is missing ("not configured") or not http(s), or when the server's Coolify
    (`server_base_url`, whose app uuids these are) is at another origin."""
    if not token:
        return TokenReach(token_present=False)
    if not base_url or not base_url.strip():
        return TokenReach(
            token_present=True,
            errors=["coolify: not configured (no COOLIFY_BASE_URL in the profile's .env)"],
        )
    origin = _origin(base_url)
    if origin is None:
        return TokenReach(token_present=True, errors=["coolify: COOLIFY_BASE_URL is not usable"])
    if server_base_url is not None and _origin(server_base_url) != origin:
        return TokenReach(
            token_present=True, errors=["coolify: the profile's Coolify is not Tumnis's Coolify"]
        )
    base = base_url.strip().rstrip("/")
    own, foreign = _targets(own), _targets(foreign)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    async def one(app: str) -> Answer:
        if not _APP.fullmatch(app):
            return False, f"coolify {app[:64]}: not an application uuid"
        try:
            answer = await client.get(
                f"{base}/api/v1/applications/{app}", headers=headers, timeout=PROBE_TIMEOUT_S
            )
        except httpx.TimeoutException:
            return None, f"coolify {app}: timed out"
        except httpx.HTTPError:
            return None, f"coolify {app}: request failed"
        if answer.status_code == httpx.codes.OK:
            return True, None
        if answer.status_code in {
            httpx.codes.UNAUTHORIZED,
            httpx.codes.FORBIDDEN,
            httpx.codes.NOT_FOUND,
        }:
            return False, None
        return None, f"coolify {app}: HTTP {answer.status_code}"

    reachable, foreign_hits, errors = _reach(own, foreign, await _gathered([*own, *foreign], one))
    return TokenReach(
        token_present=True,
        own_reachable=reachable,
        foreign_reachable=foreign_hits,
        errors=errors,
    )


async def token_reach(
    profile: Path,
    *,
    own_repos: Sequence[str],
    foreign_repos: Sequence[str],
    own_apps: Sequence[str],
    foreign_apps: Sequence[str],
    coolify_base_url: str | None,
) -> tuple[TokenReach | None, TokenReach | None]:
    """The GitHub and Coolify reach of the profile's tokens; None for a kind with nothing
    to probe."""
    env = read_env(profile)
    github: TokenReach | None = None
    coolify: TokenReach | None = None
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S, follow_redirects=False) as client:
        if own_repos or foreign_repos:
            github = await probe_github(
                client, _first(env, GITHUB_TOKEN_VARS), own=own_repos, foreign=foreign_repos
            )
        if own_apps or foreign_apps:
            coolify = await probe_coolify(
                client,
                _first(env, COOLIFY_URL_VARS),
                _first(env, COOLIFY_TOKEN_VARS),
                own=own_apps,
                foreign=foreign_apps,
                server_base_url=coolify_base_url,
            )
    return github, coolify
