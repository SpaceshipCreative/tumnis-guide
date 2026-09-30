"""A conflict's outside copy is taken only while the file still holds what the plan saw
(P1-15, row 10): a file changed or gone since the plan leaves the decision to the next sync,
before anything is written."""

import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest

from tumnis.modules.knowledge.adapters.fake import FakeStorage
from tumnis.modules.knowledge.sync import _Apply, _Stale
from tumnis.modules.knowledge.sync_rules import Action

PATH = "Acme/notes/Plan.md"


async def _chunks(data: bytes) -> AsyncIterator[bytes]:
    yield data


def _item(etag: str, content_hash: str | None) -> dict[str, Any]:
    return {
        "path": PATH,
        "rename_from": None,
        "project_id": str(uuid.uuid4()),
        "decision": {"action": Action.CONFLICT_KEEP_BOTH.value, "if_match": etag},
        "prev": None,
        "remote": {
            "size": 1,
            "mtime": datetime(2026, 3, 9, tzinfo=UTC).isoformat(),
            "etag": etag,
            "content_hash": content_hash,
        },
        "local": None,
    }


def _apply(backend: FakeStorage, item: dict[str, Any]) -> _Apply:
    return _Apply(
        None,  # type: ignore[arg-type]  # planned_bytes never touches the session
        backend=backend,
        location_id=uuid.uuid4(),
        item=item,
        record=None,
        doc=None,
    )


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-15")
async def test_planned_bytes_are_read_while_unchanged() -> None:
    backend = FakeStorage()
    stat = await backend.write(PATH, _chunks(b"outside edit"), None)
    digest = hashlib.sha256(b"outside edit").hexdigest()
    assert await _apply(backend, _item(stat.etag, digest)).planned_bytes() == b"outside edit"
    assert await _apply(backend, _item(stat.etag, None)).planned_bytes() == b"outside edit"


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-15")
async def test_changed_or_gone_since_the_plan_is_stale() -> None:
    backend = FakeStorage()
    planned = await backend.write(PATH, _chunks(b"outside edit"), None)
    digest = hashlib.sha256(b"outside edit").hexdigest()
    await backend.write(PATH, _chunks(b"edited again"), planned.etag)
    with pytest.raises(_Stale):
        await _apply(backend, _item(planned.etag, digest)).planned_bytes()

    current = await backend.stat(PATH)
    assert current is not None
    with pytest.raises(_Stale):  # same etag, other bytes than the plan hashed
        await _apply(backend, _item(current.etag, digest)).planned_bytes()

    await backend.delete(PATH)
    with pytest.raises(_Stale):
        await _apply(backend, _item(current.etag, digest)).planned_bytes()
