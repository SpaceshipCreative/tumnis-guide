"""`HostedEmbeddings`: an optional hosted OpenAI-compatible embeddings provider (P3-10,
FR-11.10). The same protocol as `VllmEmbeddings`, with the provider's key sent as a bearer
token and `hosted = True`, so `decisions.api.embed` never routes a local-only project's
text to it (Data flow rule 6). Built lazily by the registry; never imported by the api
process (import-linter `api-never-calls-out`)."""

from __future__ import annotations

from tumnis.modules.decisions.adapters.embeddings.vllm import VllmEmbeddings

__all__ = ["HostedEmbeddings"]


class HostedEmbeddings(VllmEmbeddings):
    name = "decisions.embeddings_hosted"
    hosted: bool = True
