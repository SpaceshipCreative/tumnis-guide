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
TITLE_CHARS: Final = 300  # the two fields that leave the server, and their caps
PROJECT_NAME_CHARS: Final = 120
SPOKEN_SYSTEM_PROMPT: Final = (
    "Rewrite this message as one short sentence to be read aloud. No preamble."
)
SPOKEN_TEXT_CHARS: Final = 500  # plan defaults
MAX_SPOKEN_CHARS: Final = 200
SPOKEN_MAX_TOKENS: Final = 96


def placeholder_prompt(title: str, project_name: str) -> str:
    """The user message: the task title and the project name, capped, and nothing else
    from the task (Data flow rule 6)."""
    return f"Task: {title[:TITLE_CHARS]}\nProject: {project_name[:PROJECT_NAME_CHARS]}"


async def placeholder_first_action(
    *, title: str, project_name: str, project_id: UUID
) -> str | None:
    """Returns one imperative sentence or None on timeout, error, or a local-only project with
    no local endpoint. Sends title (300) and project name (120) only. Worker-only."""
    text = await _complete(
        "placeholder",
        project_id,
        system=SYSTEM_PROMPT,
        user=placeholder_prompt(title, project_name),
        max_tokens=PLACEHOLDER_MAX_TOKENS,
        timeout_ms=generation_config.settings().placeholder_timeout_ms,
    )
    return None if text is None else one_line(text, MAX_PLACEHOLDER_CHARS)


async def spoken_focus_message(*, text: str, project_id: UUID) -> str | None:
    """The spoken form of a focus message the master did not supply (P2-16's caller): one
    sentence of at most 200 characters, or None on timeout or error (the caller speaks the
    in-app text, FR-10.8). Sends the message text only (500). Worker-only."""
    spoken = await _complete(
        "spoken",
        project_id,
        system=SPOKEN_SYSTEM_PROMPT,
        user=text[:SPOKEN_TEXT_CHARS],
        max_tokens=SPOKEN_MAX_TOKENS,
        timeout_ms=generation_config.settings().spoken_timeout_ms,
    )
    return None if spoken is None else one_line(spoken, MAX_SPOKEN_CHARS)


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
