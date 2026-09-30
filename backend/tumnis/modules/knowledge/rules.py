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
    raise NotImplementedError


def low_confidence_pages(
    grades: Mapping[int, str | tuple[str, str]], text_items: Mapping[int, int]
) -> list[int]:
    """Pages whose Docling mean or low grade is POOR (any case; a grade is one value or a
    (mean, low) pair), or with no text items at all (a page missing from `text_items` has
    none); sorted, once each."""
    raise NotImplementedError


def chunk_pages(prov_pages: Iterable[int]) -> tuple[int | None, int | None]:
    """(lowest, highest) page a chunk's items came from, or (None, None) without any."""
    raise NotImplementedError


def upload_file_name(name: str) -> str:
    """The stored name of an upload: the basename only, NFC, control characters removed,
    `/ \\ : * ? " < > |` replaced with '-', spaces and dots trimmed at both ends, a Windows
    reserved name (CON, PRN, AUX, NUL, COM1-9, LPT1-9) given a trailing '_', cut to 200
    bytes keeping the extension, empty -> 'untitled'. The result passes `safe_rel_path`."""
    raise NotImplementedError


def numbered_name(name: str, attempt: int) -> str:
    """The `attempt`-th candidate for a taken name: 1 is the name itself, then 'stem 2.ext',
    'stem 3.ext' (the extension is the last suffix; none: the number goes at the end)."""
    raise NotImplementedError
