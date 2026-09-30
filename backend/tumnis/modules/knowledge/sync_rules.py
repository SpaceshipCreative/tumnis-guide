"""The folder sync's pure rules (P1-15, FR-15.12): no I/O, the day passed in.

`decide_sync_action` decides every case of the architecture's sync table from three views
of one path: the record of the last sync (`Prev`, a `folder_files` row), what the folder
shows now (`Remote`) and the linked Document now (`Local`). Tumnis never overwrites a file
it did not create, and every write it decides carries a precondition: the etag the folder
shows now, or create-only.

Also here: rename pairing, conflict names, file names Tumnis gives (sanitized for every
filesystem a folder may live on, de-duplicated ignoring case), and the `tumnis_id`
frontmatter that keeps a note's identity through an outside rename.
"""

import re
import unicodedata
from collections.abc import Callable, Sequence
from datetime import date, datetime
from enum import StrEnum
from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel

from tumnis.modules.knowledge.rules import LOOKALIKE_DOTS, LOOKALIKE_SEPARATORS

Origin = Literal["tumnis", "external"]
ReviewKind = Literal[
    "sync_conflict", "deleted_outside_edited_inside", "edited_outside_deleted_inside"
]


class Prev(BaseModel, frozen=True):
    """A `folder_files` row: the path's state at the last sync."""

    path: str
    size: int
    mtime: datetime
    etag: str
    content_hash: str
    origin: Origin
    document_id: UUID | None
    synced_version: int | None


class Remote(BaseModel, frozen=True):
    """What the folder shows at the path now."""

    size: int
    mtime: datetime
    etag: str
    content_hash: str | None  # None: metadata equal to prev, hash not computed


class Local(BaseModel, frozen=True):
    """The linked Document in Tumnis now."""

    document_id: UUID
    version: int
    content_hash: str
    origin: Origin
    trashed: bool
    delete_confirmed: bool


class Action(StrEnum):
    NOOP = "noop"
    UPDATE_STAT = "update_stat"
    CREATE_DOCUMENT = "create_document"
    NEW_VERSION_FROM_FOLDER = "new_version_from_folder"
    WRITE_THROUGH = "write_through"
    WRITE_NEW = "write_new"
    ADOPT = "adopt"
    CONFLICT_KEEP_BOTH = "conflict_keep_both"
    TRASH_DOCUMENT = "trash_document"
    REWRITE_FROM_TUMNIS = "rewrite_from_tumnis"
    MOVE_TO_TUMNIS_TRASH = "move_to_tumnis_trash"
    UNINDEX_ONLY = "unindex_only"
    DELETE_AT_SOURCE = "delete_at_source"
    RESTORE_DOCUMENT = "restore_document"
    FORGET = "forget"


class SyncDecision(BaseModel, frozen=True):
    action: Action
    if_match: str | None = None  # precondition for any write
    conflict_path: str | None = None
    review_kind: ReviewKind | None = None
    taint_new_document: bool = False


_NOOP: Final = SyncDecision(action=Action.NOOP)


def decide_sync_action(  # noqa: PLR0911, PLR0912  # one return per row of the table
    prev: Prev | None,
    local: Local | None,
    remote: Remote | None,
    *,
    path: str,
    today_local: date,
    siblings: frozenset[str],
) -> SyncDecision:
    """The one decision for `path` (rows 1 to 20 of the plan's table). A file counts as
    outside (`external`) when its record or its Document says so; such a file is never
    written, rewritten or moved to Tumnis's trash, and deleted only after the user's
    in-app confirmation."""
    external = (prev is not None and prev.origin == "external") or (
        local is not None and local.origin == "external"
    )

    def conflict(if_match: str | None) -> SyncDecision:
        return SyncDecision(
            action=Action.CONFLICT_KEEP_BOTH,
            if_match=if_match,
            conflict_path=conflict_name(path, today_local, siblings),
            review_kind="sync_conflict",
        )

    if prev is None:
        if remote is None:
            if local is None or local.trashed:
                return _NOOP  # row 20
            return SyncDecision(action=Action.WRITE_NEW)  # row 2: create-only
        if local is None or local.trashed:
            return SyncDecision(action=Action.CREATE_DOCUMENT, taint_new_document=True)  # 1
        if remote.content_hash is not None and remote.content_hash == local.content_hash:
            return SyncDecision(action=Action.ADOPT)  # row 3
        return conflict(None)  # row 4

    local_changed = (
        local is not None
        and not local.trashed
        and local.version > (prev.synced_version or 0)
        and local.content_hash != prev.content_hash
    )
    if remote is None:
        if local is None or local.trashed:
            return SyncDecision(action=Action.FORGET)  # rows 18, 19
        if not local_changed:
            return SyncDecision(action=Action.TRASH_DOCUMENT)  # row 12
        if external:  # never rewrite an outside file: the Document goes, with a review
            return SyncDecision(
                action=Action.TRASH_DOCUMENT, review_kind="deleted_outside_edited_inside"
            )
        return SyncDecision(
            action=Action.REWRITE_FROM_TUMNIS, review_kind="deleted_outside_edited_inside"
        )  # row 13

    if remote.content_hash is not None:
        remote_changed = remote.content_hash != prev.content_hash
    else:
        remote_changed = remote.etag != prev.etag
    meta_only = not remote_changed and (remote.size, remote.mtime, remote.etag) != (
        prev.size,
        prev.mtime,
        prev.etag,
    )
    if local is None:
        if remote_changed:
            return SyncDecision(action=Action.CREATE_DOCUMENT, taint_new_document=True)
        return _NOOP
    if local.trashed:
        if remote_changed:
            return SyncDecision(
                action=Action.RESTORE_DOCUMENT, review_kind="edited_outside_deleted_inside"
            )  # row 17
        if external:
            if local.delete_confirmed:
                return SyncDecision(action=Action.DELETE_AT_SOURCE, if_match=remote.etag)  # 16
            return SyncDecision(action=Action.UNINDEX_ONLY)  # row 15
        return SyncDecision(action=Action.MOVE_TO_TUMNIS_TRASH, if_match=remote.etag)  # row 14
    if local_changed:
        if external:
            return conflict(None)  # rows 9, 11: the outside file stays untouched
        if remote_changed:
            return conflict(remote.etag)  # row 10
        return SyncDecision(action=Action.WRITE_THROUGH, if_match=remote.etag)  # row 8
    if remote_changed:
        return SyncDecision(action=Action.NEW_VERSION_FROM_FOLDER)  # row 7
    if meta_only:
        return SyncDecision(action=Action.UPDATE_STAT)  # row 6
    return _NOOP  # row 5


# --- Rename pairing ---------------------------------------------------------------------


def pair_renames(
    missing: Sequence[Prev], new: Sequence[tuple[str, Remote, UUID | None]]
) -> list[tuple[str, str]]:
    """Pairs a missing path with a new path: first by `tumnis_id` (notes), then by equal
    content hash when exactly one candidate matches on each side. Paired files keep their
    Document (no trash, no new document)."""
    pairs: list[tuple[str, str]] = []
    left = list(missing)
    free = list(new)
    for prev in list(left):
        if prev.document_id is None:
            continue
        by_id = [item for item in free if item[2] == prev.document_id]
        if len(by_id) == 1:
            pairs.append((prev.path, by_id[0][0]))
            left.remove(prev)
            free.remove(by_id[0])
    for prev in left:
        same = [item for item in free if item[1].content_hash == prev.content_hash]
        rivals = [p for p in left if p.content_hash == prev.content_hash]
        if len(same) == 1 and len(rivals) == 1:
            pairs.append((prev.path, same[0][0]))
            free.remove(same[0])
    return pairs


# --- Notes and their identity -----------------------------------------------------------

_FRONTMATTER: Final = re.compile(r"\A---\r?\n(.*?)^---[ \t]*(?:\r?\n|\Z)", re.DOTALL | re.MULTILINE)
_ID_LINE: Final = re.compile(r"^tumnis_id:[ \t]*(\S+)[ \t]*\r?\n?", re.MULTILINE)


def read_tumnis_id(markdown_head: str) -> UUID | None:
    """The `tumnis_id` key of a note's frontmatter; None without one, or not a UUID."""
    block = _FRONTMATTER.match(markdown_head)
    if block is None:
        return None
    found = _ID_LINE.search(block.group(1))
    if found is None:
        return None
    try:
        return UUID(found.group(1).strip("'\""))
    except ValueError:
        return None


def render_note(document_id: UUID, body: str) -> str:
    """The file Tumnis writes for a note: its body under frontmatter whose first key is
    `tumnis_id` (the note keeps its identity when renamed outside). A body that has
    frontmatter of its own keeps it, with the id added first."""
    if _FRONTMATTER.match(body):
        newline = "\r\n" if body.startswith("---\r\n") else "\n"
        head = len("---") + len(newline)
        return f"{body[:head]}tumnis_id: {document_id}{newline}{body[head:]}"
    return f"---\ntumnis_id: {document_id}\n---\n{body}"


def note_body(text: str) -> str:
    """A note file's body as Tumnis stores it: the `tumnis_id` line left out of its
    frontmatter, and the frontmatter left out when nothing else is in it (the inverse of
    `render_note`)."""
    block = _FRONTMATTER.match(text)
    if block is None or _ID_LINE.search(block.group(1)) is None:
        return text
    rest = _ID_LINE.sub("", block.group(1), count=1)
    if not rest.strip():
        return text[block.end() :]
    start = block.start(1)
    return text[:start] + rest + text[block.end(1) :]


# --- Names --------------------------------------------------------------------------------

MAX_NAME_BYTES: Final = 200  # plan default; a number or a conflict marker may be added
_FORBIDDEN_IN_NAME: Final = frozenset('/\\:*?"<>|')
_ALLOWED_FORMAT: Final = frozenset("‌‍")  # as safe_rel_path allows
_SPECIAL: Final = frozenset("./")
_RESERVED: Final = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)", re.IGNORECASE)


def _clean_char(ch: str) -> str:
    category = unicodedata.category(ch)
    if category in {"Cc", "Cs"} or (category == "Cf" and ch not in _ALLOWED_FORMAT):
        return ""
    if ch in _FORBIDDEN_IN_NAME or ch in LOOKALIKE_SEPARATORS or ch in LOOKALIKE_DOTS:
        return "-"
    if ch not in _SPECIAL and _SPECIAL & set(unicodedata.normalize("NFKC", ch)):
        return "-"  # reads as '.' or '/' once compatibility-normalized
    return ch


def _split_ext(name: str) -> tuple[str, str]:
    """(stem, extension): the extension is the last suffix; a dotfile or a name without a
    suffix has none."""
    dot = name.rfind(".")
    return (name[:dot], name[dot:]) if dot > 0 else (name, "")


def _cut(name: str, limit: int) -> str:
    """`name` within `limit` bytes of UTF-8, shortening the stem and keeping the extension
    (whole characters only); an "extension" over a quarter of the limit is not one."""
    if len(name.encode()) <= limit:
        return name
    stem, ext = _split_ext(name)
    if len(ext.encode()) > limit // 4:
        stem, ext = name, ""
    room = limit - len(ext.encode())
    return stem.encode()[:room].decode(errors="ignore").rstrip(" .") + ext


def sanitize_filename(name: str) -> str:
    """NFC; control and format characters dropped; `/ \\ : * ? " < > |` and look-alike
    separators or dots replaced with '-'; spaces and dots trimmed at both ends; a leading
    '~' replaced with '-'; Windows reserved names (CON, PRN, AUX, NUL, COM1-9, LPT1-9,
    before the first dot) given a trailing '_'; cut to 200 bytes keeping the extension;
    empty -> 'untitled'. The result passes `safe_rel_path` unchanged."""
    cleaned = "".join(_clean_char(ch) for ch in unicodedata.normalize("NFC", name))
    cleaned = unicodedata.normalize("NFC", cleaned).strip(" .")
    if cleaned.startswith("~"):
        cleaned = "-" + cleaned[1:]
    if reserved := _RESERVED.match(cleaned):
        cleaned = f"{cleaned[: reserved.end(1)]}_{cleaned[reserved.end(1) :]}"
    cleaned = unicodedata.normalize("NFC", _cut(cleaned, MAX_NAME_BYTES))
    return cleaned or "untitled"


def numbered_name(name: str, n: int) -> str:
    """'Plan.md', 2 -> 'Plan 2.md'; the number goes before the extension (none: at the end);
    1 is the name itself."""
    if n <= 1:
        return name
    stem, ext = _split_ext(name)
    return f"{stem} {n}{ext}"


def _free(path: str, make: Callable[[str, int], str], taken: frozenset[str]) -> str:
    folder, _, name = path.rpartition("/")
    prefix = f"{folder}/" if folder else ""
    n = 1
    while True:
        candidate = prefix + make(name, n)
        if candidate.casefold() not in taken:
            return candidate
        n += 1


def dedupe_name(name: str, taken_casefolded: frozenset[str]) -> str:
    """'Plan.md' -> 'Plan 2.md', 'Plan 3.md' … while the name is taken (compared ignoring
    case). A path keeps its folders; only its last segment is numbered."""
    return _free(name, numbered_name, taken_casefolded)


def conflict_name(path: str, day: date, siblings: frozenset[str]) -> str:
    """'notes/plan.md' -> 'notes/plan (conflict 2026-03-09).md'; if taken (ignoring case),
    '… (conflict 2026-03-09) 2.md', 3, … The extension is the last suffix; dotfiles and
    names without a suffix get the marker at the end."""
    marker = f"(conflict {day.isoformat()})"

    def make(name: str, n: int) -> str:
        stem, ext = _split_ext(name)
        return numbered_name(f"{stem} {marker}", n) + ext

    return _free(path, make, frozenset(s.casefold() for s in siblings))
