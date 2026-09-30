"""The Jev MCP server for Tumnis profiles (P1-05, FR-11.6): one stdio tool, `ask`, that
forwards a state and typed questions to TypeSafe's Jev through the pinned `typesafe-sdk`.

TypeSafe publishes client SDKs, not an MCP server, so the profiles carry this one, pinned
and versioned with them (profiles/<name>/mcp.json runs it at a tagged version). The API
key comes from the profile's .env (TYPESAFE_API_KEY) and is never logged. Every request
carries a pinned model version, never an alias. Phase 1 skills call no tools; P2-12 is
the first to use it.
"""

import logging
import os
import re
from typing import Any, Final

from mcp.server.mcpserver import MCPServer
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, Score

PINNED_MODEL: Final = "jev-1.13.0"  # as scripts/record_jev.py; bumped with the SDK
_PINNED: Final = re.compile(r"^jev-\d+\.\d+\.\d+$")
_QUESTION_TYPES: Final = {"choice": Choice, "score": Score, "noul": Noul}

# The SDK logs request and response bodies at DEBUG; they must never reach a log.
logging.getLogger("typesafe_sdk").setLevel(logging.INFO)

server = MCPServer(
    "jev",
    instructions="Ask TypeSafe's Jev typed questions (choice, score, noul) about a state.",
)


def _questions(raw: dict[str, dict[str, Any]]) -> dict[str, Choice | Score | Noul]:
    questions: dict[str, Choice | Score | Noul] = {}
    for qid, question in raw.items():
        kind = _QUESTION_TYPES.get(str(question.get("type")))
        if kind is None:
            raise ValueError(f"question {qid!r}: type is one of {sorted(_QUESTION_TYPES)}")
        questions[qid] = kind.model_validate(question)
    return questions


@server.tool()
async def ask(
    state: str | dict[str, Any],
    questions: dict[str, dict[str, Any]],
    model: str | None = None,
) -> dict[str, Any]:
    """Ask Jev about `state`. `questions` maps an id to a typed question:
    {"type": "choice", "criteria": {option: description}},
    {"type": "score", "criteria": [anchor, ...]} or {"type": "noul", "criteria": ...},
    each with optional `instructions`. `model` is a pinned version such as jev-1.13.0."""
    pinned = model or PINNED_MODEL
    if not _PINNED.fullmatch(pinned):
        raise ValueError(f"model {pinned!r} is not a pinned version (jev-X.Y.Z)")
    client = AsyncTypeSafeClient(api_key=os.environ["TYPESAFE_API_KEY"], model=pinned)
    response = await client.system_one(state, _questions(questions), model=pinned)
    answers: dict[str, Any] = response.model_dump(mode="json", exclude_none=True)
    return answers


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
