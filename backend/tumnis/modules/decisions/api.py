"""decisions public functions and DTOs; the only file other modules may import.

P1-01: the typed answers every decisions provider returns, the decisions slot's provider
config (primary, fallback, pinned model and the credential sealed with the workspace data
key), and `ask_raw`, the one call P1-02's `decide` makes to a provider once it has routed
the question (it builds the whitelisted request, refuses an oversized one, and asks).
Routing, thresholds, fallback, the decision log, the cache and the rate limit belong to
`decide` (P1-02).
"""

from collections.abc import Mapping
from typing import Any, Final, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.settings_store import open_for_workspace, seal_for_workspace
from tumnis.core.tenancy import WorkspaceContext, session_for
from tumnis.modules.decisions.adapters.port import (
    ChoiceAnswer,
    DecisionsProvider,
    NoulAnswer,
    ProviderName,
    ProviderResponse,
    ScoreAnswer,
    TypedAnswer,
)
from tumnis.modules.decisions.catalog import CATALOGUE, DecisionPoint, build_request
from tumnis.modules.decisions.models import ProviderConfig as ProviderConfigRow
from tumnis.modules.decisions.rules import is_pinned_model

__all__ = [
    "ChoiceAnswer",
    "DecisionPoint",
    "DecisionsProvider",
    "NoulAnswer",
    "ProviderConfig",
    "ProviderConfigIn",
    "ProviderResponse",
    "ScoreAnswer",
    "Slot",
    "TypedAnswer",
    "ask_raw",
    "get_provider_config",
    "put_provider_config",
]

Slot = Literal["decisions", "generation", "speech", "embeddings"]
PINNED_JEV_DEFAULT: Final = "jev-1.13.0"  # plan default; the row's model_version wins


def credential_aad(workspace_id: UUID, slot: str) -> bytes:
    """Binds a sealed credential to its workspace and slot: a blob copied elsewhere fails."""
    return f"tumnis:provider_config:v1:{workspace_id}:{slot}".encode()


class ProviderConfigIn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    slot: Slot
    primary: ProviderName
    fallback: ProviderName | None = None
    model_version: str
    api_key: SecretStr | None = None

    @model_validator(mode="after")
    def _pinned(self) -> Self:
        if self.primary == "jev" and not is_pinned_model(self.model_version):
            raise ValueError(
                f"model_version must be a pinned versioned id, not {self.model_version!r}"
            )
        return self


class ProviderConfig(ProviderConfigIn):
    version: int


_UPSERT: Final = text(
    """
    INSERT INTO provider_configs (slot, "primary", fallback, model_version, credentials_enc)
    VALUES (:slot, :primary, :fallback, :model_version, :credentials_enc)
    ON CONFLICT (workspace_id, slot)
    DO UPDATE SET "primary" = EXCLUDED."primary", fallback = EXCLUDED.fallback,
                  model_version = EXCLUDED.model_version,
                  credentials_enc = EXCLUDED.credentials_enc,
                  version = provider_configs.version + 1
    RETURNING version
    """
)


async def put_provider_config(
    ctx: WorkspaceContext, config: ProviderConfigIn, *, session: AsyncSession | None = None
) -> int:
    """Store the slot's config (replacing any), sealing the key; returns the row version."""
    async with session_for(ctx, session) as s:
        sealed = None
        if config.api_key is not None:
            _, sealed = await seal_for_workspace(
                s,
                ctx.workspace_id,
                config.api_key.get_secret_value().encode(),
                aad=credential_aad(ctx.workspace_id, config.slot),
            )
        params = config.model_dump(include={"slot", "primary", "fallback", "model_version"})
        written = await s.execute(_UPSERT, {**params, "credentials_enc": sealed})
        return int(written.scalar_one())


async def get_provider_config(
    ctx: WorkspaceContext, slot: Slot, *, session: AsyncSession | None = None
) -> ProviderConfig | None:
    """The slot's config with its key opened, or None when the slot is not configured."""
    t = ProviderConfigRow
    async with session_for(ctx, session) as s:
        row = (
            await s.execute(select(t).where(t.slot == slot, t.deleted_at.is_(None)))
        ).scalar_one_or_none()
        if row is None:
            return None
        api_key = None
        if row.credentials_enc is not None:
            opened = await open_for_workspace(
                s,
                ctx.workspace_id,
                bytes(row.credentials_enc),
                aad=credential_aad(ctx.workspace_id, slot),
            )
            api_key = SecretStr(opened.decode())
        return ProviderConfig.model_validate(
            {
                "slot": row.slot,
                "primary": row.primary,
                "fallback": row.fallback,
                "model_version": row.model_version,
                "api_key": api_key,
                "version": row.version,
            }
        )


async def ask_raw(
    provider: DecisionsProvider,
    point: DecisionPoint,
    inputs: Mapping[str, Any],
    *,
    model: str,
    timeout_ms: int | None = None,
) -> ProviderResponse:
    """Build the whitelisted request for `point` (MissingDecisionInput,
    DecisionRequestTooLarge before any call) and ask `provider` with the pinned `model`.
    No routing, logging of the decision, caching or rate limiting: `decide` (P1-02) adds
    them around this call."""
    req = build_request(point, inputs)
    timeout = CATALOGUE[point].timeout_ms if timeout_ms is None else timeout_ms
    return await provider.ask(req, model=model, timeout_ms=timeout)
