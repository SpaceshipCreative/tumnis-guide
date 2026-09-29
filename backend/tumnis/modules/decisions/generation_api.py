"""The Generation slot (P1-03, FR-11.8): the only entry points to free-text generation.

Two functions, for the few Tumnis-side strings that must not wait on Hermes: the
placeholder first action shown while the project agent's real one is pending (P1-08's
enrichment workflow) and the spoken form of a focus message the master did not supply
(P2-16). Never planning, triage reasoning or orchestration: import-linter's
`generation-callers` contract lets only `agents.workflows` and `notifications` import this
file. Worker-only: the provider makes an outbound call.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

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

# Fixed text, never a planning or triage prompt (FR-11.8, design decision 8).
SYSTEM_PROMPT: Final = "Write one short imperative first step for this task. No preamble."
MAX_PLACEHOLDER_CHARS: Final = 120  # plan default
PLACEHOLDER_MAX_TOKENS: Final = 48  # plan default: one short sentence


async def placeholder_first_action(
    *, title: str, project_name: str, project_id: UUID
) -> str | None:
    """Returns one imperative sentence or None on timeout, error, or a local-only project with
    no local endpoint. Sends title (300) and project name (120) only. Worker-only."""
    provider = generation_config.provider()
    if provider is None:
        return None
    text = await provider.complete(
        system=SYSTEM_PROMPT,
        user=f"Task: {title}\nProject: {project_name}",
        max_tokens=PLACEHOLDER_MAX_TOKENS,
        timeout_ms=generation_config.settings().placeholder_timeout_ms,
    )
    return one_line(text, MAX_PLACEHOLDER_CHARS)


async def spoken_focus_message(*, text: str, project_id: UUID) -> str | None:
    """The spoken form of a focus message (P2-16's caller), or None."""
    raise NotImplementedError
