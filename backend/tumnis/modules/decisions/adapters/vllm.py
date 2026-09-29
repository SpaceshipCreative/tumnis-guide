"""`VllmDecisions`: the decisions slot's fallback, a local vLLM behind the OpenAI-compatible
API (P1-02, FR-11.3). Filled in by P1-02's implementation."""

from typing import Any


class VllmDecisions:
    name = "decisions.vllm"

    def __init__(self, base_url: str, **deps: Any) -> None:
        raise NotImplementedError
