"""The audit redactor's exact allowlist of keys that look sensitive but hold no secret
(P3-09: a purge's `context_items` count)."""

from __future__ import annotations

import pytest


@pytest.mark.req("SEC-3")
@pytest.mark.wp("P3-09")
def test_context_items_count_stays_other_context_keys_redacted() -> None:
    """Only the exact key `context_items` is let through; "context" and "context_text",
    which may hold user text, are still redacted, nested too."""
    from tumnis.core.audit import REDACTED, redact_details  # noqa: PLC0415

    details = {
        "counts": {"context_items": 3},
        "context_items": 3,
        "context": "secret",
        "context_text": "x",
        "nested": {"context": "secret", "context_text": "x"},
    }

    assert redact_details(details) == {
        "counts": {"context_items": 3},
        "context_items": 3,
        "context": REDACTED,
        "context_text": REDACTED,
        "nested": {"context": REDACTED, "context_text": REDACTED},
    }
