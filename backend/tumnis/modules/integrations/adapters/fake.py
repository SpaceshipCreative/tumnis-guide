"""`ScriptedConnector`: a scriptable connector for tests and fake mode (P0-12).

`script_pages([...])` queues the pages `sync` hands out in order (then one empty, final
page); `calls` records every `sync` cursor. `map` turns the scripted payloads into
message, thread, note, person and artifact records (pure); `mapper=` replaces it, for
tests that need records of another type.
"""

from collections.abc import Callable, Iterable, Sequence
from typing import Any

from tumnis.core.adapters.registry import Health
from tumnis.core.canonical import CanonicalRecord
from tumnis.core.sanitize import sanitize_html
from tumnis.modules.integrations.api import (
    ArtifactRecord,
    Capability,
    ConnectorKind,
    MessageRecord,
    NoteRecord,
    PersonRecord,
    RawItem,
    SyncPage,
    ThreadRecord,
)

Mapper = Callable[[RawItem], Sequence[CanonicalRecord]]


class ScriptedConnector:
    kind: ConnectorKind = "email"
    provider: str = "scripted"
    capabilities: frozenset[Capability] = frozenset({"poll", "read"})

    def __init__(
        self,
        *,
        kind: ConnectorKind = "email",
        provider: str = "scripted",
        mapper: Mapper | None = None,
    ) -> None:
        self.kind = kind
        self.provider = provider
        self._mapper = mapper
        self._pages: list[SyncPage] = []
        self.calls: list[dict[str, Any] | None] = []

    def script_pages(self, pages: Iterable[SyncPage]) -> None:
        self._pages.extend(pages)

    async def sync(self, cursor: dict[str, Any] | None) -> SyncPage:
        self.calls.append(cursor)
        if self._pages:
            return self._pages.pop(0)
        return SyncPage(items=[], next_cursor=None, has_more=False)

    def map(self, raw: RawItem) -> list[CanonicalRecord]:
        if self._mapper is not None:
            return list(self._mapper(raw))
        builder = _BUILDERS.get(raw.record_type)
        if builder is None:
            raise ValueError(f"the scripted connector does not map {raw.record_type!r} items")
        return builder(raw)

    async def health(self) -> Health:
        return "ok"


def _message(raw: RawItem) -> list[CanonicalRecord]:
    p = raw.payload
    thread = p.get("thread")
    html = p.get("html")
    message = MessageRecord(
        external_id=raw.external_id,
        fetched_at=raw.fetched_at,
        provider_url=p.get("url"),
        thread_external_id=thread["id"] if thread else None,
        sent_at=p.get("sent_at"),
        from_addr=p.get("from"),
        to_addrs=p.get("to", []),
        subject=p.get("subject"),
        body_text=p.get("text"),
        body_html_sanitized=sanitize_html(html) if html is not None else None,
        labels=p.get("labels", []),
    )
    if not thread:
        return [message]
    return [
        ThreadRecord(
            external_id=thread["id"],
            fetched_at=raw.fetched_at,
            subject=thread.get("subject"),
            participants=thread.get("participants", []),
            last_message_at=message.sent_at,
        ),
        message,
    ]


def _note(raw: RawItem) -> list[CanonicalRecord]:
    p = raw.payload
    return [
        NoteRecord(
            external_id=raw.external_id,
            fetched_at=raw.fetched_at,
            provider_url=p.get("url"),
            title=p.get("title"),
            start_at=p.get("start"),
            end_at=p.get("end"),
            attendees=p.get("attendees", []),
            body_text=p.get("text"),
            action_items=p.get("action_items", []),
            event_external_id=p.get("event_id"),
        )
    ]


def _person(raw: RawItem) -> list[CanonicalRecord]:
    p = raw.payload
    primary = str(p["email"]).lower()
    emails = sorted({primary, *(str(e).lower() for e in p.get("emails", []))})
    return [
        PersonRecord(
            external_id=raw.external_id,
            fetched_at=raw.fetched_at,
            display_name=p.get("name"),
            primary_email=primary,
            emails=emails,
            domains=sorted({email.rpartition("@")[2] for email in emails}),
        )
    ]


def _artifact(raw: RawItem) -> list[CanonicalRecord]:
    p = raw.payload
    return [
        ArtifactRecord(
            external_id=raw.external_id,
            fetched_at=raw.fetched_at,
            provider_url=p.get("url"),
            kind=p["kind"],
            url=p.get("url"),
            state=p.get("state"),
            checks=p.get("checks", {}),
        )
    ]


_BUILDERS: dict[str, Callable[[RawItem], list[CanonicalRecord]]] = {
    "message": _message,
    "note": _note,
    "person": _person,
    "artifact": _artifact,
}
