"""Deployment-level settings only (pydantic-settings), the preview guard and the boot checks.

Everything per workspace lives encrypted in `workspace_settings` (P0-08); this is only what
a deployment needs to start. Secrets never sit in the environment: the only secret-related
variables are file paths (AGENTS.md, Never).
"""

import asyncio
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Literal, Self

from psycopg import errors as pg_errors
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tumnis.core.crypto import (
    PEPPER_FILE,
    MasterKeys,
    configure_master_keys,
    configure_peppers,
    load_master_keys,
)
from tumnis.core.net import NetPolicy, parse_allowlist
from tumnis.core.types import DeploymentMode


class SettingsError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


EXIT_CONFIG = 78  # EX_CONFIG from sysexits.h; the CLI exits with it on SettingsError
DBOS_DATABASE = "tumnis_dbos"
DeploymentEnv = Literal["dev", "preview", "prod"]
# Every database URL Settings holds; prod requires verify-full on each (P0-16).
DATABASE_URL_FIELDS = (
    "database_url",
    "database_direct_url",
    "database_owner_url",
    "dbos_system_database_url",
)
# The boot checks wait for Postgres (a restart can race the database): 30 tries, 2 s apart.
BOOT_DB_ATTEMPTS = 30
BOOT_DB_RETRY_S = 2.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    database_url: str
    database_direct_url: str
    database_owner_url: str | None = None
    dbos_system_database_url: str | None = None  # default: direct URL, database tumnis_dbos
    deployment_mode: DeploymentMode = "self-hosted"
    deployment_env: DeploymentEnv = "dev"
    tumnis_adapters: Literal["real", "fake"] = "real"
    master_key_file: str = "/run/secrets/tumnis_master_key"
    api_key_pepper_file: str = "/run/secrets/tumnis_pepper"
    typesafe_api_key: SecretStr | None = Field(default=None, alias="TYPESAFE_API_KEY")
    cache_backend: Literal["memory", "redis"] = "memory"
    redis_url: str | None = None
    tumnis_disabled_modules: str = ""  # comma-separated deployment kill list (P0-08)
    # Base URL users reach (links, callbacks) and the only origin allowed to post to the
    # sign-in routes (P0-13); unset, the request's own origin is used.
    public_base_url: str | None = None
    metrics_token_file: str | None = None  # bearer for /metrics; required in prod (P0-27)
    sentry_dsn: str | None = None  # GlitchTip; unset keeps the SDK off (P0-27)
    # Comma-separated CIDR ranges (or addresses) the SSRF guard allows in hosted mode even
    # though they are private (P0-16, SEC-5); self-hosted mode allows the LAN anyway.
    outbound_allowlist: str = ""

    @model_validator(mode="after")
    def _preview_guard(self) -> Self:
        """A preview runs the seed set on fakes and must never hold a production secret."""
        if self.deployment_env == "preview":
            if self.tumnis_adapters != "fake":
                raise SettingsError(
                    "preview_requires_fakes", "preview runs only with TUMNIS_ADAPTERS=fake"
                )
            if self.typesafe_api_key is not None:
                raise SettingsError("preview_has_production_secret", "a Jev key is set in preview")
        if self.deployment_mode == "hosted" and not (self.public_base_url or "").startswith(
            "https://"
        ):
            raise SettingsError(
                "hosted_requires_https", "DEPLOYMENT_MODE=hosted needs an https:// PUBLIC_BASE_URL"
            )
        try:
            parse_allowlist(self.outbound_allowlist.split(","))
        except ValueError as exc:
            raise SettingsError("outbound_allowlist_invalid", str(exc)) from exc
        if self.cache_backend == "redis":
            raise SettingsError(
                "cache_backend_unavailable", "the redis cache backend arrives with hosted mode"
            )
        return self

    def net_policy(self) -> NetPolicy:
        """The SSRF guard's policy for this deployment (tumnis.core.net.guarded_client)."""
        return NetPolicy(
            mode=self.deployment_mode,
            allowlist=parse_allowlist(self.outbound_allowlist.split(",")),
        )

    def check_database_tls(self) -> None:
        """Prod reaches Postgres and PgBouncer only over TLS that verifies the server
        (P0-16, SEC-9): every database URL set must say `sslmode=verify-full` (with
        `sslrootcert`), or startup fails with `database_tls_required`. Dev and preview
        accept `require` (and the test databases run without TLS). The api, the worker and
        migrate call this at startup, after the /metrics token check."""
        if self.deployment_env != "prod":
            return
        for field in DATABASE_URL_FIELDS:
            url = getattr(self, field)
            if url is None:
                continue
            mode = make_url(url).query.get("sslmode")
            if mode != "verify-full":
                raise SettingsError(
                    "database_tls_required",
                    f"{field.upper()} must use sslmode=verify-full in prod (has {mode or 'none'})",
                )

    @cached_property
    def master_keys(self) -> MasterKeys:
        """The master key file, loaded and checked once (MasterKeyError when unsafe). The
        owner check applies in prod only: tests and dev run as whoever owns their files."""
        return load_master_keys(self.master_key_file, strict_owner=self.deployment_env == "prod")

    @cached_property
    def peppers(self) -> MasterKeys:
        """The pepper file (API_KEY_PEPPER_FILE), loaded and checked once like the master
        key file (P0-13)."""
        return load_master_keys(
            self.api_key_pepper_file,
            strict_owner=self.deployment_env == "prod",
            what=PEPPER_FILE,
        )

    def metrics_token(self) -> str | None:
        """The /metrics bearer token from METRICS_TOKEN_FILE, stripped (P0-27, FR-12.3).
        Prod refuses to start without one; elsewhere no file means no token, and /metrics
        answers 401 to everyone. A configured file that is missing or empty is an error."""
        if not self.metrics_token_file:  # unset, or "" (compose.preview.yaml)
            if self.deployment_env == "prod":
                raise SettingsError(
                    "metrics_token_file_required",
                    "prod serves /metrics only behind a bearer token: set METRICS_TOKEN_FILE",
                )
            return None
        try:
            token = Path(self.metrics_token_file).read_text().strip()
        except OSError as exc:
            raise SettingsError(
                "metrics_token_unreadable", f"{self.metrics_token_file}: {exc.strerror}"
            ) from exc
        if not token:
            raise SettingsError("metrics_token_unreadable", f"{self.metrics_token_file} is empty")
        return token

    @property
    def dbos_system_url(self) -> str:
        """DBOS system database: DBOS_SYSTEM_DATABASE_URL, else the direct URL (never
        PgBouncer) with the database name `tumnis_dbos`."""
        if self.dbos_system_database_url:
            return self.dbos_system_database_url
        direct = make_url(self.database_direct_url).set(database=DBOS_DATABASE)
        return direct.render_as_string(hide_password=False)


def install_master_keys(settings: Settings) -> MasterKeys | None:
    """Point tumnis.core.crypto at the deployment's key file, and load and check the file now
    (MasterKeyError) in prod and whenever it exists. In dev and preview a missing file fails
    only at the first secret read or write, so stacks without secrets need no key file."""
    configure_master_keys(lambda: settings.master_keys)
    if settings.deployment_env == "prod" or Path(settings.master_key_file).exists():
        return settings.master_keys
    return None


def install_peppers(settings: Settings) -> MasterKeys | None:
    """Point tumnis.core.crypto at the deployment's pepper file, loaded and checked now in
    prod and whenever it exists (MasterKeyError); in dev and preview a missing file fails
    only at the first sign-in."""
    configure_peppers(lambda: settings.peppers)
    if settings.deployment_env == "prod" or Path(settings.api_key_pepper_file).exists():
        return settings.peppers
    return None


# --- Deployment marker ------------------------------------------------------------------


@dataclass(frozen=True)
class Marker:
    """One row of the global `deployment_marker` table."""

    env: str
    master_key_fingerprint: str | None = None


def master_key_fingerprint(path: str) -> str | None:
    """sha256 of the master key file, or None when there is no readable key file."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def check_markers(settings: Settings, markers: Sequence[Marker]) -> None:
    """Refuse a database that belongs to another deployment."""
    if not markers:
        raise SettingsError(
            "deployment_marker_missing", "the database has no deployment marker; run migrate"
        )
    if settings.deployment_env == "preview":
        fingerprint = master_key_fingerprint(settings.master_key_file)
        prod_prints = {m.master_key_fingerprint for m in markers if m.env == "prod"}
        if fingerprint is not None and fingerprint in prod_prints:
            raise SettingsError(
                "preview_uses_prod_master_key", "the preview master key is production's"
            )
        if any(m.env == "prod" for m in markers):
            raise SettingsError(
                "preview_on_prod_database", "a preview must never open the production database"
            )
    others = sorted({m.env for m in markers} - {settings.deployment_env})
    if others:
        raise SettingsError(
            "deployment_env_mismatch",
            f"DEPLOYMENT_ENV={settings.deployment_env} but the database is marked {others}",
        )


# Before migrate has run: no schema app, no reader function or no table behind it.
_MARKER_MISSING = (
    pg_errors.InvalidSchemaName,
    pg_errors.UndefinedFunction,
    pg_errors.UndefinedTable,
)


async def read_markers(url: str) -> list[Marker]:
    """The marker rows, read as the (owner-free) app role on the direct URL through the
    SECURITY DEFINER function app.deployment_markers() (P0-06: no table grant)."""
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        for attempt in range(1, BOOT_DB_ATTEMPTS + 1):
            try:
                async with engine.connect() as conn:
                    rows = await conn.execute(
                        text("SELECT env, master_key_fingerprint FROM app.deployment_markers()")
                    )
                    return [Marker(env, fingerprint) for env, fingerprint in rows]
            except ProgrammingError as exc:
                if isinstance(exc.orig, _MARKER_MISSING):
                    raise SettingsError(
                        "deployment_marker_missing", "no deployment_marker table; run migrate"
                    ) from exc
                raise SettingsError("deployment_marker_unreadable", str(exc.orig)) from exc
            except OperationalError:
                if attempt == BOOT_DB_ATTEMPTS:
                    raise
                await asyncio.sleep(BOOT_DB_RETRY_S)
    finally:
        await engine.dispose()
    raise AssertionError("unreachable")  # pragma: no cover


# workspace_settings keys that hold a real provider credential; a preview refuses to boot on
# a database holding any of them (A0.5). Provider slots join as their WPs land (P1-01).
PROVIDER_SECRET_KEYS: tuple[str, ...] = ("decisions.jev",)


async def read_provider_settings(url: str, keys: Sequence[str] = PROVIDER_SECRET_KEYS) -> list[str]:
    """Which of `keys` any workspace holds in workspace_settings, read as the app role
    through the SECURITY DEFINER function app.provider_setting_keys() (names only)."""
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT k FROM app.provider_setting_keys(:keys) AS k"), {"keys": list(keys)}
            )
            return sorted(key for (key,) in rows)
    except ProgrammingError as exc:
        detail = f"cannot read workspace_settings: {exc.orig}"
        raise SettingsError("migration_pending", detail) from exc
    finally:
        await engine.dispose()


async def boot_checks(settings: Settings) -> None:
    """Runs before the api or worker serves anything. Reads deployment_marker as the
    owner-free app role: preview refuses a database whose marker env is 'prod', or whose
    master key fingerprint matches prod's; every env refuses a marker whose env differs
    from DEPLOYMENT_ENV; preview refuses any workspace_settings row for a real provider
    slot (checked from P0-08 on)."""
    check_markers(settings, await read_markers(settings.database_direct_url))
    if settings.deployment_env == "preview":
        held = await read_provider_settings(settings.database_direct_url)
        if held:
            raise SettingsError(
                "preview_has_production_secret", f"workspace_settings holds {', '.join(held)}"
            )
