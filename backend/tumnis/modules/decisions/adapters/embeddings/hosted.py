"""`HostedEmbeddings`: an optional hosted OpenAI-compatible embeddings provider (P3-10,
FR-11.10). The same protocol as `VllmEmbeddings`, with the provider's key sent as a bearer
token and `hosted = True`, so `decisions.api.embed` never routes a local-only project's
text to it (Data flow rule 6). The Embeddings slot builds it from the server's .env
(`EMBEDDINGS__HOSTED_BASE_URL`, `EMBEDDINGS__HOSTED_MODEL`, `EMBEDDINGS__HOSTED_DIMS`,
`EMBEDDINGS__HOSTED_API_KEY`; Scott decision 75): the key comes from Settings, never from
the database. Built lazily by the registry; never imported by the api process
(import-linter `api-never-calls-out`)."""

from __future__ import annotations

from tumnis.modules.decisions.adapters.embeddings.vllm import VllmEmbeddings

__all__ = ["HostedEmbeddings"]


class HostedEmbeddings(VllmEmbeddings):
    name = "decisions.embeddings_hosted"
    hosted: bool = True
