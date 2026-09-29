"""Deployment-level settings only (pydantic-settings), the preview guard and the boot checks.

Everything per workspace lives encrypted in `workspace_settings` (P0-08); this is only what
a deployment needs to start. Secrets never sit in the environment: the only secret-related
variables are file paths (AGENTS.md, Never).
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class SettingsError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


EXIT_CONFIG = 78  # EX_CONFIG from sysexits.h; the CLI exits with it on SettingsError
DBOS_DATABASE = "tumnis_dbos"
DeploymentEnv = Literal["dev", "preview", "prod"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    database_url: str
    database_direct_url: str
    database_owner_url: str | None = None
    dbos_system_database_url: str | None = None  # default: direct URL, database tumnis_dbos
    deployment_mode: Literal["self-hosted", "hosted"] = "self-hosted"
    deployment_env: DeploymentEnv = "dev"
    tumnis_adapters: Literal["real", "fake"] = "real"
    master_key_file: str = "/run/secrets/tumnis_master_key"
    api_key_pepper_file: str = "/run/secrets/tumnis_pepper"
    typesafe_api_key: SecretStr | None = Field(default=None, alias="TYPESAFE_API_KEY")

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
        return self

    @property
    def dbos_system_url(self) -> str:
        """DBOS system database: DBOS_SYSTEM_DATABASE_URL, else the direct URL (never
        PgBouncer) with the database name `tumnis_dbos`."""
        if self.dbos_system_database_url:
            return self.dbos_system_database_url
        direct = make_url(self.database_direct_url).set(database=DBOS_DATABASE)
        return direct.render_as_string(hide_password=False)


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


async def boot_checks(settings: Settings) -> None:
    """Runs before the api or worker serves anything (P0-04)."""
    raise NotImplementedError
