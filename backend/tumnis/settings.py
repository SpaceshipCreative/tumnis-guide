"""Deployment-level settings only (pydantic-settings). Interfaces from P0-04; the preview
guard and the boot checks land with their spec tests."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class SettingsError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


EXIT_CONFIG = 78  # EX_CONFIG from sysexits.h; the CLI exits with it on SettingsError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    database_url: str
    database_direct_url: str
    database_owner_url: str | None = None
    dbos_system_database_url: str | None = None  # default: direct URL, database tumnis_dbos
    deployment_mode: Literal["self-hosted", "hosted"] = "self-hosted"
    deployment_env: Literal["dev", "preview", "prod"] = "dev"
    tumnis_adapters: Literal["real", "fake"] = "real"
    master_key_file: str = "/run/secrets/tumnis_master_key"
    api_key_pepper_file: str = "/run/secrets/tumnis_pepper"
    typesafe_api_key: SecretStr | None = Field(default=None, alias="TYPESAFE_API_KEY")


async def boot_checks(settings: Settings) -> None:
    """Runs before the api or worker serves anything (P0-04)."""
    raise NotImplementedError
