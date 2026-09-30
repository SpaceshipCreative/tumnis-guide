"""knowledge event payload models (P1-16); `events.py` re-exports them. They live apart so
`api.py` and `store.py` can emit them without importing `events.py` (which imports `api`).

- `document.added`: a document's first version finished extraction and is searchable.
- `document.changed`: a later version of a document did, and is now its current version.

Both carry what a subscriber needs to index without reading the row again; the Markdown and
the chunks are read from Postgres.
"""

from typing import ClassVar, Literal
from uuid import UUID

from tumnis.core.events import EventPayload, event_type


class _DocumentEvent(EventPayload):
    document_id: UUID
    version_id: UUID
    version_no: int
    project_id: UUID | None
    title: str
    trust: Literal["trusted", "untrusted"]
    size: int


@event_type("document.added", 1)
class DocumentAddedV1(_DocumentEvent):
    event_name: ClassVar[str] = "document.added"
    schema_version: Literal[1] = 1


@event_type("document.changed", 1)
class DocumentChangedV1(_DocumentEvent):
    event_name: ClassVar[str] = "document.changed"
    schema_version: Literal[1] = 1
