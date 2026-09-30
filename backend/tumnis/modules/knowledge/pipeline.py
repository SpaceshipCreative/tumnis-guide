"""The extraction pipeline's step bodies (P1-16, SEC-10, FR-15.2, ADR-0007).

`workflows.extract_document` is the DBOS workflow: it calls each function named in `STEPS`
as `pipeline.<step>(...)`, looked up at call time so a test can wrap a step (the step log)
and so nothing else needs to know how a step works. Arguments and results are small JSON
values (ids as strings, a `Ref` for the file); large outputs (the converted document, its
Markdown, the chunks) go to `extraction_artifacts`, never through DBOS.

The scanner, the extractor and the vision model come from `use()` (tests) or, in a real
deployment, the adapter registry and the settings given to `configure()`; in fakes mode
(`TUMNIS_ADAPTERS=fake`) the fakes.
"""

from typing import Any, Final, Literal, TypedDict

from tumnis.core.net import NetPolicy
from tumnis.settings import KnowledgeSettings

STEPS: Final = (
    "read",
    "scan",
    "quarantine",
    "sniff",
    "place",
    "convert",
    "vlm",
    "store",
    "chunk",
    "index",
    "emit",
    "fail",
)
Source = Literal["spool", "storage"]


class Ref(TypedDict):
    """The file as step 1 read it: its content hash and size, and the name it arrived
    under (the upload's, or the folder file's)."""

    sha256: str
    size: int
    name: str
    source: Source


_settings = KnowledgeSettings()
_net = NetPolicy(mode="self-hosted")
_use: dict[str, Any] = {"scanner": None, "extractor": None, "vision": None}


def configure(settings: KnowledgeSettings, *, net: NetPolicy | None = None) -> KnowledgeSettings:
    """Set the spool and scratch folders, clamd's address and the SSRF policy the worker
    reaches storage under; returns the previous settings."""
    global _settings, _net  # noqa: PLW0603  # process-wide, like the adapter registry
    previous = _settings
    _settings = settings
    if net is not None:
        _net = net
    return previous


def use(**parts: Any) -> dict[str, Any]:
    """Swap the `scanner`, `extractor` and `vision` the steps use (tests; None puts the
    default back); returns the previous values of the parts given, ready for `use(**...)`."""
    previous = {name: _use[name] for name in parts}
    _use.update(parts)
    return previous


async def read(workspace_id: str, version_id: str, source: Source) -> Ref:
    """1. Stream the file to `<scratch>/<version_id>` hashing it; the upload's own spool
    file, or the document's file on its location."""
    raise NotImplementedError


async def scan(workspace_id: str, version_id: str, ref: Ref) -> dict[str, Any]:
    """2. Stream the scratch copy through clamd: {"infected": bool, "signature": str|None}.
    A clean scan moves the version to `extracting`."""
    raise NotImplementedError


async def quarantine(workspace_id: str, version_id: str, signature: str, source: Source) -> None:
    """2b. Version and document `quarantined`, one `upload.quarantined` audit row, the spool
    and scratch copies removed. A file in a project folder is never touched."""
    raise NotImplementedError


async def sniff(workspace_id: str, version_id: str, ref: Ref) -> dict[str, Any]:
    """3. libmagic on the first 8 KiB, the size limit and `classify_type`:
    {"kind": str|None, "mime": str, "refusal": str|None}."""
    raise NotImplementedError


async def place(workspace_id: str, version_id: str, ref: Ref) -> str:
    """3b. (uploads) Write the scratch copy to `uploads/<name>` in the document's folder,
    create-only; returns the path relative to the folder."""
    raise NotImplementedError


async def convert(workspace_id: str, version_id: str, ref: Ref, kind: str) -> dict[str, Any]:
    """4. The extractor converts the scratch copy; the document and its Markdown are stored
    as artifacts: {"low_pages": [int, ...]}."""
    raise NotImplementedError


async def vlm(workspace_id: str, version_id: str, ref: Ref, low_pages: list[int]) -> None:
    """5. The vision model reads each low-confidence page; its Markdown is stored."""
    raise NotImplementedError


async def store(workspace_id: str, version_id: str) -> None:
    """6. The compressed document and its Markdown export go on the version."""
    raise NotImplementedError


async def chunk(workspace_id: str, version_id: str) -> int:
    """7. Chunk the document (vision pages replace the standard chunks of their page);
    the chunks are stored as an artifact; returns how many."""
    raise NotImplementedError


async def index(workspace_id: str, version_id: str) -> int:
    """8. The chunks become `chunks` rows, once (a rerun replaces them)."""
    raise NotImplementedError


async def emit(workspace_id: str, version_id: str) -> str:
    """9. Version and document `ready`, the version current, and `document.added` (or
    `document.changed` for a document that had a current version) in the same transaction."""
    raise NotImplementedError


async def fail(workspace_id: str, version_id: str, code: str) -> None:
    """A refused or failed file: version and document `failed` with the code as the reason;
    the spool and scratch copies removed."""
    raise NotImplementedError
