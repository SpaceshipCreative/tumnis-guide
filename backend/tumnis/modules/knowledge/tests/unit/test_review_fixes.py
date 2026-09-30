"""Review follow-ups on P1-16 (PR #69): a second `file` part is refused rather than kept as a
text field, and a pipeline step that names a missing version fails instead of reporting
success."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from starlette.requests import Request

from tumnis.core.errors import ProblemError
from tumnis.core.versioning import NotFound
from tumnis.modules.knowledge import store
from tumnis.modules.knowledge.uploads import spool_upload

BOUNDARY = "----tumnis-review"


def _request(body: bytes) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/knowledge/documents",
        "headers": [(b"content-type", f"multipart/form-data; boundary={BOUNDARY}".encode())],
    }
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _file_part(name: str, content: bytes) -> bytes:
    return (
        (
            f"--{BOUNDARY}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        + content
        + b"\r\n"
    )


async def _no_check(fields: dict[str, str]) -> None:
    return None


@pytest.mark.req("SEC-10")
@pytest.mark.wp("P1-16")
async def test_second_file_part_is_refused(tmp_path: Path) -> None:
    """Two parts named `file`: 422 `invalid_upload`, and nothing is left in the spool."""
    body = _file_part("a.txt", b"first") + _file_part("b.txt", b"second")
    body += f"--{BOUNDARY}--\r\n".encode()
    dest = tmp_path / "spool" / "v1"

    with pytest.raises(ProblemError) as caught:
        await spool_upload(_request(body), dest, on_file=_no_check)

    assert caught.value.problem.code == "invalid_upload"
    assert not dest.exists()


@pytest.mark.req("SEC-10")
@pytest.mark.wp("P1-16")
async def test_one_file_part_is_spooled(tmp_path: Path) -> None:
    """The ordinary case still works: one file part lands at `dest`."""
    body = _file_part("a.txt", b"first") + f"--{BOUNDARY}--\r\n".encode()
    dest = tmp_path / "spool" / "v1"

    spooled = await spool_upload(_request(body), dest, on_file=_no_check)

    assert spooled.name == "a.txt"
    assert dest.read_bytes() == b"first"


class _NoRows:
    """A session whose UPDATE ... RETURNING matches nothing."""

    def __init__(self) -> None:
        self.executed: list[Any] = []

    async def scalar(self, statement: Any) -> None:
        return None

    async def execute(self, statement: Any) -> None:
        self.executed.append(statement)


@pytest.mark.req("FR-15.2")
@pytest.mark.wp("P1-16")
async def test_set_content_on_a_missing_version_raises() -> None:
    session = _NoRows()
    with pytest.raises(NotFound):
        await store.set_content(session, uuid4(), digest=bytes(32), size=1)  # type: ignore[arg-type]
    assert session.executed == []


@pytest.mark.req("FR-15.2")
@pytest.mark.wp("P1-16")
async def test_set_sniffed_on_a_missing_version_raises() -> None:
    session = _NoRows()
    with pytest.raises(NotFound):
        await store.set_sniffed(session, uuid4(), mime="text/plain", kind="text")  # type: ignore[arg-type]
    assert session.executed == []
