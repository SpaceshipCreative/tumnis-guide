"""The Speech slot (P4-03, FR-10.8, FR-11.7, Data flow rule 6): voice settings, `speak` (a
focus message's exact text made into a WAV clip by the server engine) and the clips it
keeps for `CLIP_TTL_MIN` minutes. Re-exported by `decisions.api`.

- Settings > Voice is the workspace setting `voice` (`rules.VoiceSettings`): off by default,
  the browser's own speech synthesis as the engine. Only `engine: server` makes clips here.
- Engines: the worker configures the slot from `Settings.speech` (`configure_speech`): a
  local Piper HTTP server, or nothing (unset `SPEECH__PIPER_URL`: no server engine, so the
  PWA speaks with the browser's voice). Under `TUMNIS_ADAPTERS=fake` a configured Piper
  answers with the fake. A hosted provider is never configured from the environment (its
  key storage is a Scott item). Tests install engines with `use_speech`.
- Routing (`rules.speech_route`): hosted only when the workspace allowed it and never for a
  local-only project; Piper is local.
- `speak` runs in the worker (the notifications subscriber); the real engines are built
  lazily through the registry, so the api process never imports them (import-linter
  `api-never-calls-out`). It synthesizes outside any transaction, then stores the clip
  (one per message: a redelivery replaces it) and announces `speech_clip` live so the
  focus bar refetches. Expired clips are deleted as new ones are stored, and never served.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Table, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tumnis.core.adapters.registry import current_mode, resolve
from tumnis.core.clock import SystemClock
from tumnis.core.live import mark_changed
from tumnis.core.net import NetPolicy
from tumnis.core.settings_store import SettingSection, get_setting, register_section
from tumnis.core.tenancy import WorkspaceContext, session_for, tenant_session
from tumnis.core.versioning import NotFound
from tumnis.modules.decisions.adapters.speech.base import (
    CLIP_TTL_MIN,
    MAX_SPOKEN_CHARS,
    TTS_TIMEOUT_S,
    WAV_MIME,
    SpeechTTS,
)
from tumnis.modules.decisions.adapters.speech.fake import FakeTTS
from tumnis.modules.decisions.models import SpeechClip
from tumnis.modules.decisions.rules import SpeechRoute, VoiceSettings, speech_route
from tumnis.modules.projects import api as projects
from tumnis.settings import SpeechSettings

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "CLIP_TTL_MIN",
    "MAX_SPOKEN_CHARS",
    "SPEECH_LIVE_ENTITY",
    "TTS_TIMEOUT_S",
    "VOICE_SECTION",
    "Clip",
    "ClipAudio",
    "clip_ids",
    "configure_speech",
    "get_clip",
    "speak",
    "use_speech",
    "voice_settings",
]

VOICE_SECTION: Final = "voice"
SPEECH_LIVE_ENTITY: Final = "speech_clip"  # its id is the message's: the focus bar refetches
PIPER_ADAPTER: Final = "decisions.speech_piper"

register_section(SettingSection(VOICE_SECTION, VoiceSettings))

_clips: Table = SpeechClip.__table__  # type: ignore[assignment]


class Clip(BaseModel):
    """A stored clip: `GET /v1/speech/clips/{id}` serves it until `expires_at`."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    message_id: UUID
    mime: str
    expires_at: datetime
    provider: SpeechRoute


@dataclass(frozen=True)
class ClipAudio:
    mime: str
    audio: bytes


@dataclass
class _Slot:
    settings: SpeechSettings = field(default_factory=SpeechSettings)
    net_policy: NetPolicy = field(default_factory=lambda: NetPolicy(mode="self-hosted"))
    built: dict[str, SpeechTTS] | None = None


_slot = _Slot()
_override: list[Mapping[str, SpeechTTS] | None] = [None]


def configure_speech(settings: SpeechSettings, *, net_policy: NetPolicy | None) -> None:
    """The worker's call at start: the engines are built from `settings` on first use."""
    global _slot  # noqa: PLW0603  # one Speech slot per process
    _slot = _Slot(settings, net_policy or NetPolicy(mode="self-hosted"))


def use_speech(engines: Mapping[str, SpeechTTS] | None) -> None:
    """Tests: answer with these engines (by route: "piper", "hosted") until reset with
    None."""
    _override[0] = engines


def _build(cfg: SpeechSettings, net_policy: NetPolicy) -> dict[str, SpeechTTS]:
    if cfg.piper_url is None:
        return {}
    if current_mode() == "fake":
        return {"piper": FakeTTS()}
    engine: SpeechTTS = resolve(
        PIPER_ADAPTER,
        "real",
        base_url=cfg.piper_url,
        voice=cfg.piper_voice,
        clock=SystemClock(),
        net_policy=net_policy,
    )
    return {"piper": engine}


def _engines() -> Mapping[str, SpeechTTS]:
    if _override[0] is not None:
        return _override[0]
    if _slot.built is None:
        _slot.built = _build(_slot.settings, _slot.net_policy)
    return _slot.built


async def voice_settings(ctx: WorkspaceContext) -> VoiceSettings:
    """The workspace's voice settings (the defaults when never set)."""
    found = await get_setting(ctx, VOICE_SECTION, VoiceSettings)
    return VoiceSettings() if found is None else found.value


async def speak(  # the message's facts, spelled out
    ctx: WorkspaceContext,
    text: str,
    voice: str | None = None,
    *,
    message_id: UUID,
    now: datetime,
    project_id: UUID | None = None,
) -> Clip | None:
    """`text` (exactly what the app shows) spoken by the workspace's server engine and kept
    as the message's clip until `now + CLIP_TTL_MIN`; None when the route is the browser
    (nothing to make here) or no engine serves it. Raises the adapter errors. Never logs
    the text."""
    settings = await voice_settings(ctx)
    local_only = (
        project_id is not None
        and settings.server_provider == "hosted"
        and await _local_only(ctx, project_id)
    )
    route = speech_route(settings, local_only=local_only)
    if route == "browser":
        return None
    engine = _engines().get(route)
    if engine is None:
        return None
    audio = await engine.synthesize(text, voice)
    expires_at = now + timedelta(minutes=CLIP_TTL_MIN)
    async with tenant_session(ctx) as s:
        await s.execute(delete(_clips).where(_clips.c.expires_at <= now))
        clip_id: UUID = (
            await s.execute(
                pg_insert(_clips)
                .values(message_id=message_id, mime=WAV_MIME, audio=audio, expires_at=expires_at)
                .on_conflict_do_update(
                    index_elements=["workspace_id", "message_id"],
                    set_={"audio": audio, "mime": WAV_MIME, "expires_at": expires_at},
                )
                .returning(_clips.c.id)
            )
        ).scalar_one()
        mark_changed(s, SPEECH_LIVE_ENTITY, message_id)
    return Clip(
        id=clip_id, message_id=message_id, mime=WAV_MIME, expires_at=expires_at, provider=route
    )


async def _local_only(ctx: WorkspaceContext, project_id: UUID) -> bool:
    async with tenant_session(ctx) as s:
        return await projects.local_decisions_only(project_id, session=s)


async def clip_ids(
    ctx: WorkspaceContext,
    message_ids: Sequence[UUID],
    now: datetime,
    *,
    session: AsyncSession | None = None,
) -> dict[UUID, UUID]:
    """The live clip of each message that has one: message id -> clip id."""
    if not message_ids:
        return {}
    async with session_for(ctx, session) as s:
        rows = await s.execute(
            select(_clips.c.message_id, _clips.c.id).where(
                _clips.c.message_id.in_(list(message_ids)), _clips.c.expires_at > now
            )
        )
        return {row.message_id: row.id for row in rows}


async def get_clip(ctx: WorkspaceContext, clip_id: UUID, now: datetime) -> ClipAudio:
    """The clip's audio; NotFound when it does not exist in this workspace or expired."""
    async with tenant_session(ctx) as s:
        row = (
            await s.execute(
                select(_clips.c.mime, _clips.c.audio).where(
                    _clips.c.id == clip_id, _clips.c.expires_at > now
                )
            )
        ).one_or_none()
    if row is None:
        raise NotFound("speech_clips", clip_id)
    return ClipAudio(mime=row.mime, audio=bytes(row.audio))
