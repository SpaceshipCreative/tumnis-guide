"""The packet's size cap and comment taint (P2-02 review follow-ups, SAF-1): the rendered
prompt never passes PACKET_MAX_BYTES, and a comment written by an API key with no run is
tainted (R-31)."""

from __future__ import annotations

from typing import Any

import pytest

NONCE = "u-00112233445566ff"


def _inputs(*, comments: list[str] | None = None, items: list[str] | None = None) -> Any:
    from tumnis.modules.agents.packet_builder import PacketInputs  # noqa: PLC0415

    return PacketInputs.model_validate(
        {
            "task": {
                "id": "01950000-0000-7000-8000-00000000a001",
                "parent_id": None,
                "title": "Draft the Acme status note",
                "acceptance_criteria": None,
                "label": "ai",
                "estimate_minutes": None,
                "status": "today",
                "tainted": False,
                "by_user": True,
            },
            "comments": [
                {"author": "api_key:01950000-0000-7000-8000-00000000c001", "body": body}
                for body in comments or []
            ],
            "project": {
                "id": "01950000-0000-7000-8000-00000000b001",
                "name": "Acme site",
                "brief_md": "Keep the Acme site running.",
            },
            "context_items": [
                {
                    "id": f"01950000-0000-7000-8000-0000000e{n:04d}",
                    "target_type": "message",
                    "source": "email",
                    "text": text,
                }
                for n, text in enumerate(items or [])
            ],
            "policy": {"gated": ["send_email"], "allowed": ["read"], "time_cap_minutes": 60},
        }
    )


def _assemble(inputs: Any) -> Any:
    from tumnis.modules.agents.packet_builder import assemble  # noqa: PLC0415
    from tumnis.modules.agents.rules import RunKind  # noqa: PLC0415

    return assemble(
        inputs,
        kind=RunKind.TASK,
        run_id="01950000-0000-7000-8000-00000000f001",
        profile_id="01950000-0000-7000-8000-00000000f002",
        nonce=NONCE,
        token=None,
    )


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-02")
def test_context_items_that_grow_when_escaped_stay_within_the_packet_cap() -> None:
    """Context text is budgeted by its rendered (escaped) size, so items made of characters
    that escape to several bytes are cut further and the prompt stays within the cap."""
    from tumnis.modules.agents.rules import (  # noqa: PLC0415
        CONTEXT_ITEM_MAX_BYTES,
        PACKET_MAX_BYTES,
    )

    packet = _assemble(_inputs(items=["<" * CONTEXT_ITEM_MAX_BYTES] * 12))
    assert len(packet.prompt_text.encode("utf-8")) <= PACKET_MAX_BYTES
    assert any(item["truncated"] for item in packet.body["context_items"])


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-02")
def test_a_packet_over_the_cap_is_refused() -> None:
    """Text outside the context budget (here, comments) that pushes the rendered prompt past
    PACKET_MAX_BYTES is refused with a clear error, never sent cut in half."""
    from tumnis.modules.agents.rules import PACKET_MAX_BYTES  # noqa: PLC0415

    with pytest.raises(ValueError, match=f"exceeds {PACKET_MAX_BYTES} bytes"):
        _assemble(_inputs(comments=["<" * 8192] * 50))


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-02")
@pytest.mark.parametrize(
    ("author", "tainted"),
    [
        ("user:01950000-0000-7000-8000-000000000001", False),
        ("task_token:01950000-0000-7000-8000-000000000002", False),
        ("api_key:01950000-0000-7000-8000-000000000003", True),
    ],
)
def test_a_comment_by_a_key_with_no_run_is_tainted(author: str, tainted: bool) -> None:
    """R-31: a write by an API key (never bound to a run) is tainted, so its comment is a
    tainted block; a person's comment is not, and a task token's follows its run (P2-08)."""
    from tumnis.modules.agents.rules import comment_tainted  # noqa: PLC0415

    assert comment_tainted(author) is tainted
