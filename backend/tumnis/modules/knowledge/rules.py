"""knowledge pure rules: no I/O, `now` and `tz` passed in.

Storage paths (P1-14, SEC-5): `safe_rel_path` is the one gate every storage path passes
before any backend touches it. The storage errors live here, not in `storage.py`, because
rules may import only their own module's rules (`storage.py` re-exports them).
"""

# The names are the plan's shared contract (P1-14 interfaces), not "...Error".
# ruff: noqa: N818

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal


class StorageError(Exception):
    """Base of every storage answer (`storage.py` holds the rest)."""


class PathRejected(StorageError):
    """The path could escape the location's root, or is not one Tumnis writes."""


LOOKALIKE_SEPARATORS: Final = frozenset("\u2215\u2044\uff0f\u29f8\ufe68\uff3c\\")
LOOKALIKE_DOTS: Final = frozenset("\u2024\u2025\u2026\uff0e\ufe52")
MAX_PATH_BYTES, MAX_SEGMENT_BYTES = 1024, 255  # plan defaults


_ALLOWED_FORMAT = frozenset("\u200c\u200d")  # zero-width (non-)joiner: needed by scripts and emoji
_DRIVE = re.compile(r"^[A-Za-z]:")
_SPECIAL = frozenset("./")
_NETWORK_FS = frozenset({"cifs", "smb3", "smbfs", "nfs", "nfs4", "fuse.sshfs", "afpfs", "webdav"})


def _refuse(p: str, why: str) -> PathRejected:
    return PathRejected(f"{p!r}: {why}")


def _check_char(p: str, ch: str) -> None:
    category = unicodedata.category(ch)
    if category in {"Cc", "Cs"} or (category == "Cf" and ch not in _ALLOWED_FORMAT):
        raise _refuse(p, f"control or format character U+{ord(ch):04X}")
    if ch in LOOKALIKE_SEPARATORS or ch in LOOKALIKE_DOTS:
        raise _refuse(p, f"look-alike separator or dot U+{ord(ch):04X}")
    if ch not in _SPECIAL and _SPECIAL & set(unicodedata.normalize("NFKC", ch)):
        raise _refuse(p, f"U+{ord(ch):04X} reads as '.' or '/' once compatibility-normalized")


def safe_rel_path(p: str) -> str:
    """NFC-normalize, then refuse: empty; leading '/' or '~'; a drive letter ('C:'); NUL or
    control characters; any look-alike separator or dot; '.', '..' or empty segments
    ('a//b'); a trailing '/'; a path whose NFKC form differs from its NFC form in any '.' or
    '/'; over the byte limits. Returns the normalized relative path.

    Also refused: a '~' segment anywhere, and format characters (bidi overrides such as
    U+202E) other than the zero-width joiners some scripts need."""
    if not p:
        raise _refuse(p, "empty")
    nfc = unicodedata.normalize("NFC", p)
    if nfc[0] in "/~":
        raise _refuse(p, "absolute or home-relative")
    for ch in nfc:
        _check_char(p, ch)
    try:
        size = len(nfc.encode())
    except UnicodeEncodeError:
        raise _refuse(p, "not encodable as UTF-8") from None
    if size > MAX_PATH_BYTES:
        raise _refuse(p, f"over {MAX_PATH_BYTES} bytes")
    for segment in nfc.split("/"):
        if segment in {"", ".", "..", "~"}:
            raise _refuse(p, f"segment {segment!r}")
        if _DRIVE.match(segment):
            raise _refuse(p, "drive letter")
        if len(segment.encode()) > MAX_SEGMENT_BYTES:
            raise _refuse(p, f"a segment over {MAX_SEGMENT_BYTES} bytes")
    return nfc


def is_network_fs(fstype: str) -> bool:
    """'cifs', 'smb3', 'nfs', 'nfs4', 'fuse.sshfs' -> True (and macOS's smbfs, afpfs,
    webdav): a share where hard links may be missing and file events do not arrive."""
    return fstype.strip().lower() in _NETWORK_FS


def etag_equal(a: str | None, b: str | None) -> bool:
    """Two etags name the same content: quotes and case ignored, None equals only None."""
    if a is None or b is None:
        return a is b
    return a.strip().strip('"').lower() == b.strip().strip('"').lower()


# --- Upload safety and extraction (P1-16, SEC-10, FR-15.2) --------------------------------

MAX_UPLOAD_BYTES: Final = 50 * 1024 * 1024  # SEC-10 "50 MB", read as 50 MiB = 52,428,800 bytes
DocKind = Literal["pdf", "docx", "xlsx", "pptx", "markdown", "text", "csv", "html", "image"]
_OOXML = "application/vnd.openxmlformats-officedocument."
ALLOWED_TYPES: Final[Mapping[str, frozenset[str]]] = {  # sniffed MIME -> allowed extensions
    "application/pdf": frozenset({".pdf"}),
    _OOXML + "wordprocessingml.document": frozenset({".docx"}),
    _OOXML + "spreadsheetml.sheet": frozenset({".xlsx"}),
    _OOXML + "presentationml.presentation": frozenset({".pptx"}),
    "text/plain": frozenset({".txt", ".md", ".markdown", ".csv"}),
    "text/markdown": frozenset({".md", ".markdown"}),
    "text/csv": frozenset({".csv"}),
    "application/csv": frozenset({".csv"}),
    "text/html": frozenset({".html", ".htm"}),
    "image/png": frozenset({".png"}),
    "image/jpeg": frozenset({".jpg", ".jpeg"}),
    "image/tiff": frozenset({".tif", ".tiff"}),
    "image/webp": frozenset({".webp"}),
}


@dataclass(frozen=True)
class Refusal:
    """Why a file is refused: `type_not_allowed` (its content is not a listed type),
    `type_mismatch` (its name's extension is not one that content allows), `too_large`."""

    code: Literal["type_not_allowed", "type_mismatch", "too_large"]


def classify_type(sniffed_mime: str, filename: str) -> DocKind | Refusal:
    """Refusal('type_not_allowed') if the MIME is not listed; Refusal('type_mismatch') if the
    extension (the last suffix, any case) is not allowed for the sniffed MIME; else the kind
    (pdf, docx, xlsx, pptx, markdown, text, csv, html, image)."""
    allowed = ALLOWED_TYPES.get(sniffed_mime)
    if allowed is None:
        return Refusal("type_not_allowed")
    suffix = _suffix(filename)
    if suffix not in allowed:
        return Refusal("type_mismatch")
    if sniffed_mime == "text/plain":
        return _TEXT_KIND[suffix]
    return _KIND_BY_MIME[sniffed_mime]


_KIND_BY_MIME: Final[Mapping[str, DocKind]] = {
    "application/pdf": "pdf",
    _OOXML + "wordprocessingml.document": "docx",
    _OOXML + "spreadsheetml.sheet": "xlsx",
    _OOXML + "presentationml.presentation": "pptx",
    "text/markdown": "markdown",
    "text/csv": "csv",
    "application/csv": "csv",
    "text/html": "html",
    "image/png": "image",
    "image/jpeg": "image",
    "image/tiff": "image",
    "image/webp": "image",
}
_TEXT_KIND: Final[Mapping[str, DocKind]] = {
    ".txt": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".csv": "csv",
}


def _suffix(filename: str) -> str:
    """The last suffix of the name, lower-cased: '.pdf' for 'a.tar.PDF'; '' without one."""
    base = re.split(r"[\\/]", filename)[-1]
    dot = base.rfind(".")
    return base[dot:].lower() if dot >= 0 else ""


def low_confidence_pages(
    grades: Mapping[int, str | tuple[str, str]], text_items: Mapping[int, int]
) -> list[int]:
    """Pages whose Docling mean or low grade is POOR (any case; a grade is one value or a
    (mean, low) pair), or with no text items at all (a page missing from `text_items` has
    none); sorted, once each."""
    pages: list[int] = []
    for page in sorted({*grades, *text_items}):
        grade = grades.get(page, ())
        given = (grade,) if isinstance(grade, str) else grade
        if any(g.lower() == "poor" for g in given) or text_items.get(page, 0) == 0:
            pages.append(page)
    return pages


def chunk_pages(prov_pages: Iterable[int]) -> tuple[int | None, int | None]:
    """(lowest, highest) page a chunk's items came from, or (None, None) without any."""
    found = list(prov_pages)
    return (min(found), max(found)) if found else (None, None)


# --- Names (P1-15, P1-16) ---------------------------------------------------------------

MAX_NAME_BYTES: Final = 200  # plan default; a number or a conflict marker may be added
_FORBIDDEN_IN_NAME: Final = frozenset('/\\:*?"<>|')
_RESERVED: Final = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)", re.IGNORECASE)


def _clean_char(ch: str) -> str:
    """A name's character as it is stored: control and unwanted format characters dropped,
    what a path or a look-alike could turn into a separator or a dot replaced by '-'."""
    category = unicodedata.category(ch)
    if category in {"Cc", "Cs"} or (category == "Cf" and ch not in _ALLOWED_FORMAT):
        return ""
    if ch in _FORBIDDEN_IN_NAME or ch in LOOKALIKE_SEPARATORS or ch in LOOKALIKE_DOTS:
        return "-"
    if ch not in _SPECIAL and _SPECIAL & set(unicodedata.normalize("NFKC", ch)):
        return "-"  # reads as '.' or '/' once compatibility-normalized
    return ch


def split_ext(name: str) -> tuple[str, str]:
    """(stem, extension): the extension is the last suffix; a dotfile or a name without a
    suffix has none."""
    dot = name.rfind(".")
    return (name[:dot], name[dot:]) if dot > 0 else (name, "")


def _cut(name: str, limit: int) -> str:
    """`name` within `limit` bytes of UTF-8, shortening the stem and keeping the extension
    (whole characters only); an "extension" over a quarter of the limit is not one."""
    if len(name.encode()) <= limit:
        return name
    stem, ext = split_ext(name)
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


def upload_file_name(name: str) -> str:
    """The stored name of an upload (P1-16): the basename only (after the last '/' or
    '\\'), then `sanitize_filename`. The result passes `safe_rel_path`."""
    return sanitize_filename(re.split(r"[\\/]", unicodedata.normalize("NFC", name))[-1])


def numbered_name(name: str, n: int) -> str:
    """'Plan.md', 2 -> 'Plan 2.md'; the number goes before the extension (none: at the end);
    1 is the name itself."""
    if n <= 1:
        return name
    stem, ext = split_ext(name)
    return f"{stem} {n}{ext}"


# --- Existing folders (P3-14, FR-15.12): red-phase seams; the spec tests turn them green --


class ActorKind(StrEnum):
    user = "user"
    agent = "agent"
    system = "system"


@dataclass(frozen=True)
class WritePolicy:
    mode: Literal["tumnis_made", "existing"]
    tumnis_subdir: str = "Tumnis/"  # FR-15.12


def may_write(p: WritePolicy, path: str, origin: Literal["tumnis", "external"] | None) -> bool:
    """existing mode: only paths under Tumnis/, and never over a file whose origin is
    external. tumnis_made: anywhere in the root, never over an external file."""
    raise NotImplementedError("P3-14")


def may_rename(p: WritePolicy, src_origin: str) -> bool:
    """External files: never."""
    raise NotImplementedError("P3-14")


def may_delete(
    p: WritePolicy, origin: str, actor: ActorKind, confirmed_by_user: bool
) -> Literal["trash", "delete_at_source", "index_only", "refuse"]:
    """tumnis origin: trash. external origin: agent -> refuse; otherwise index_only without
    the user's confirmation, delete_at_source with it."""
    raise NotImplementedError("P3-14")


def trash_dir(p: WritePolicy) -> str:
    """`.tumnis/trash/` in a Tumnis-made folder; `Tumnis/.trash/` in an existing one."""
    raise NotImplementedError("P3-14")
