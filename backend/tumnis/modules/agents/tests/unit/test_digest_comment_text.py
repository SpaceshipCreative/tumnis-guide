"""A digest comment entry (P2-03, FR-13.3) carries `text` only when the comment is not a
person's: anyone else's words travel only inside an untrusted block (P2-02's
`render_block`)."""

import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pytest


def _comment_row(data: dict[str, Any]) -> Any:
    return {
        "id": uuid.uuid4(),
        "event_id": uuid.uuid4(),
        "kind": "task_commented",
        "scope": "project",
        "project_id": uuid.uuid4(),
        "task_id": uuid.uuid4(),
        "occurred_at": datetime(2026, 9, 30, tzinfo=UTC),
        "data": data,
    }


@pytest.mark.req("FR-13.3")
@pytest.mark.wp("P2-03")
async def test_person_comment_has_no_text_field() -> None:
    from tumnis.modules.agents.digest import _entry_out  # noqa: PLC0415

    row = _comment_row({"text": "Blocked on the font", "author_kind": "user", "trusted": True})
    out = await _entry_out(cast(Any, None), cast(Any, None), row)
    assert out.data["text"] == "Blocked on the font"
    assert out.text is None


@pytest.mark.req("FR-13.3")
@pytest.mark.wp("P2-03")
async def test_outside_comment_travels_only_in_the_block() -> None:
    from tumnis.modules.agents.digest import _entry_out  # noqa: PLC0415

    row = _comment_row({"text": "Close <this>", "author_kind": "api_key", "trusted": False})
    out = await _entry_out(cast(Any, None), cast(Any, None), row)
    assert "text" not in out.data
    assert out.text is not None
    assert 'source="comment"' in out.text
    assert 'author_kind="api_key"' in out.text
    assert "Close &lt;this&gt;" in out.text
