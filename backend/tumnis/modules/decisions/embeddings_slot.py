"""The Embeddings slot (P3-10, FR-11.10, Data flow rule 6): which embedders a workspace's
text may go to, and `embed`, the one way knowledge turns text into vectors. Re-exported by
`decisions.api`.

- The slot holds one or more adapters (`Embedders`), the preferred (`primary`) first. The
  worker configures it from `Settings.embeddings` (`configure_embeddings`): a local vLLM
  endpoint, or nothing (unset `EMBEDDINGS__BASE_URL`: no embedder, so hybrid search falls
  back to full text). Under `TUMNIS_ADAPTERS=fake` a configured slot answers with the fake.
  Tests install adapters directly with `use_embedders` (it wins over the configuration).
- Routing: a project set to local decisions only, or a workspace whose `embeddings`
  setting says `local_only` (the workspace knowledge base follows the workspace setting),
  never reaches a hosted embedder. With only hosted embedders, such text is not embedded
  at all (`EmbedResult(skipped=True)`).
- The real adapters are built lazily through the registry: a process that only searches
  with the slot off never imports them (import-linter `api-never-calls-out`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict

from tumnis.core import tenancy
from tumnis.core.adapters.registry import current_mode, resolve
from tumnis.core.clock import SystemClock
from tumnis.core.net import NetPolicy
from tumnis.core.settings_store import SettingSection, get_setting, register_section
from tumnis.modules.decisions.adapters.embeddings.fake import FakeEmbeddings
from tumnis.modules.projects import api as projects
from tumnis.settings import EmbeddingsSettings

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from tumnis.modules.decisions.adapters.embeddings.port import EmbeddingsAdapter

__all__ = [
    "EMBEDDINGS_SECTION",
    "EmbedResult",
    "EmbedderInfo",
    "Embedders",
    "EmbeddingsWorkspaceSettings",
    "configure_embeddings",
    "embed",
    "embedders_for",
    "use_embedders",
]

EMBEDDINGS_SECTION: Final = "embeddings"
ADAPTER: Final = "decisions.embeddings_vllm"


class EmbeddingsWorkspaceSettings(BaseModel):
    """Workspace setting `embeddings`: `local_only` keeps every text of the workspace (its
    knowledge base and all projects) away from hosted embedders."""

    local_only: bool = False


register_section(SettingSection(EMBEDDINGS_SECTION, EmbeddingsWorkspaceSettings))


@dataclass(frozen=True)
class Embedders:
    adapters: tuple[EmbeddingsAdapter, ...]
    primary: str | None = None  # the preferred adapter's model; None: the first

    def ordered(self) -> list[EmbeddingsAdapter]:
        first = [a for a in self.adapters if a.model == self.primary]
        return first + [a for a in self.adapters if a.model != self.primary]


class EmbedderInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    dims: int
    hosted: bool
    provider: str  # "vllm" | "hosted" | "fake"


class EmbedResult(BaseModel):
    """`vectors` in the order of the texts sent, from `model`; `skipped` when no allowed
    embedder exists (nothing was sent)."""

    model_config = ConfigDict(frozen=True)

    model: str | None
    dims: int | None
    vectors: list[list[float]]
    skipped: bool
    hosted: bool = False


@dataclass
class _Slot:
    settings: EmbeddingsSettings = field(default_factory=EmbeddingsSettings)
    net_policy: NetPolicy = field(default_factory=lambda: NetPolicy(mode="self-hosted"))
    built: Embedders | None = None
    done: bool = False


_slot = _Slot()
_override: list[Embedders | None] = [None]


def configure_embeddings(settings: EmbeddingsSettings, *, net_policy: NetPolicy | None) -> None:
    """The worker's call at start: the slot is built from `settings` on first use."""
    global _slot  # noqa: PLW0603  # one Embeddings slot per process
    _slot = _Slot(settings, net_policy or NetPolicy(mode="self-hosted"))


def use_embedders(embedders: Embedders | None) -> None:
    """Tests: answer with these adapters until reset with None."""
    _override[0] = embedders


def _build(cfg: EmbeddingsSettings, net_policy: NetPolicy) -> Embedders | None:
    if cfg.base_url is None:
        return None
    if current_mode() == "fake":
        return Embedders((FakeEmbeddings(cfg.model, cfg.dims),), primary=cfg.model)
    adapter: EmbeddingsAdapter = resolve(
        ADAPTER,
        "real",
        base_url=cfg.base_url,
        model=cfg.model,
        dims=cfg.dims,
        net_policy=net_policy,
        clock=SystemClock(),
    )
    return Embedders((adapter,), primary=cfg.model)


def _current() -> Embedders | None:
    if _override[0] is not None:
        return _override[0]
    if not _slot.done:
        _slot.built = _build(_slot.settings, _slot.net_policy)
        _slot.done = True
    return _slot.built


def query_timeout_s() -> float:
    """How long a search waits for its query's vector before answering with full text."""
    return _slot.settings.query_timeout_ms / 1000


def _provider(adapter: EmbeddingsAdapter) -> str:
    if isinstance(adapter, FakeEmbeddings):
        return "fake"
    return "hosted" if adapter.hosted else "vllm"


async def _local_only(project_id: UUID | None) -> bool:
    ctx = tenancy.current()
    if ctx is None:
        raise RuntimeError("decisions.embed called outside a workspace context")
    if project_id is not None and await projects.local_decisions_only(project_id):
        return True
    setting = await get_setting(ctx, EMBEDDINGS_SECTION, EmbeddingsWorkspaceSettings)
    return setting is not None and setting.value.local_only


async def _allowed(project_id: UUID | None) -> list[EmbeddingsAdapter]:
    embedders = _current()
    if embedders is None:
        return []
    adapters = embedders.ordered()
    if any(a.hosted for a in adapters) and await _local_only(project_id):
        adapters = [a for a in adapters if not a.hosted]
    return adapters


async def embedders_for(project_id: UUID | None) -> list[EmbedderInfo]:
    """The embedders this project's text may go to, the preferred first (none: the slot is
    off, or only hosted embedders exist for a local-only project). Runs in the current
    workspace context."""
    return [
        EmbedderInfo(model=a.model, dims=a.dims, hosted=a.hosted, provider=_provider(a))
        for a in await _allowed(project_id)
    ]


async def embed(
    texts: Sequence[str], project_id: UUID | None, *, model: str | None = None
) -> EmbedResult:
    """Vectors for `texts` from `model` (when allowed for the project) or the project's
    preferred allowed embedder; `skipped` when there is none. Raises the adapter errors.
    Runs in the current workspace context; never logs the texts."""
    allowed = await _allowed(project_id)
    chosen = next((a for a in allowed if a.model == model), None) if model else None
    if model is not None and chosen is None:
        return EmbedResult(model=model, dims=None, vectors=[], skipped=True)
    chosen = chosen or (allowed[0] if allowed else None)
    if chosen is None:
        return EmbedResult(model=None, dims=None, vectors=[], skipped=True)
    vectors = await chosen.embed(list(texts)) if texts else []
    return EmbedResult(
        model=chosen.model, dims=chosen.dims, vectors=vectors, skipped=False, hosted=chosen.hosted
    )
