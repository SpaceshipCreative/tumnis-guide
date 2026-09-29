"""The Generation slot (P1-03, FR-11.8): the only entry points to free-text generation.

Two functions, for the few Tumnis-side strings that must not wait on Hermes: the
placeholder first action shown while the project agent's real one is pending (P1-08's
enrichment workflow) and the spoken form of a focus message the master did not supply
(P2-16). Never planning, triage reasoning or orchestration: import-linter's
`generation-callers` contract lets only `agents.workflows` and `notifications` import this
file. Worker-only: the provider makes an outbound call.

A slow or failing endpoint never holds a caller up: past the timeout, or on any adapter
error, the answer is None and the caller shows its pending state (FR-4.6).
"""

from __future__ import annotations

import asyncio
from typing import Final
from uuid import UUID

import structlog

from tumnis.core.adapters.errors import AdapterError
from tumnis.modules.decisions import generation_config
from tumnis.modules.decisions.adapters.port import GenerationProvider
from tumnis.modules.decisions.rules import one_line

__all__ = [
    "MAX_PLACEHOLDER_CHARS",
    "SYSTEM_PROMPT",
    "GenerationProvider",
    "placeholder_first_action",
    "spoken_focus_message",
]

_log = structlog.get_logger(__name__)

# Fixed text, never a planning or triage prompt (FR-11.8, design decision 8).
SYSTEM_PROMPT: Final = "Write one short imperative first step for this task. No preamble."
MAX_PLACEHOLDER_CHARS: Final = 120  # plan default
PLACEHOLDER_MAX_TOKENS: Final = 48  # plan default: one short sentence


async def placeholder_first_action(
    *, title: str, project_name: str, project_id: UUID
) -> str | None:
    """Returns one imperative sentence or None on timeout, error, or a local-only project with
    no local endpoint. Sends title (300) and project name (120) only. Worker-only."""
    text = await _complete(
        "placeholder",
        project_id,
        system=SYSTEM_PROMPT,
        user=f"Task: {title}\nProject: {project_name}",
        max_tokens=PLACEHOLDER_MAX_TOKENS,
        timeout_ms=generation_config.settings().placeholder_timeout_ms,
    )
    return None if text is None else one_line(text, MAX_PLACEHOLDER_CHARS)


async def spoken_focus_message(*, text: str, project_id: UUID) -> str | None:
    """The spoken form of a focus message (P2-16's caller), or None."""
    raise NotImplementedError


async def _complete(
    purpose: str, project_id: UUID, *, system: str, user: str, max_tokens: int, timeout_ms: int
) -> str | None:
    """Ask the slot's provider, bounded by `timeout_ms`; None when the slot has no provider,
    the time runs out or the provider fails. The log names the purpose and the project,
    never the prompt."""
    provider = generation_config.provider()
    if provider is None:
        return None
    try:
        async with asyncio.timeout(timeout_ms / 1000):
            return await provider.complete(
                system=system, user=user, max_tokens=max_tokens, timeout_ms=timeout_ms
            )
    except (TimeoutError, AdapterError) as exc:
        _log.warning(
            "decisions.generation_skipped",
            purpose=purpose,
            project_id=str(project_id),
            error=type(exc).__name__,
        )
        return None
