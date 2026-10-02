"""knowledge pure rules: no I/O, `now` and `tz` passed in.

Storage paths (P1-14, SEC-5): `safe_rel_path` is the one gate every storage path passes
before any backend touches it. The storage errors live here, not in `storage.py`, because
rules may import only their own module's rules (`storage.py` re-exports them).
"""

# The names are the plan's shared contract (P1-14 interfaces), not "...Error".
# ruff: noqa: N818

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


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


# --- S3 buckets as a linked source (P3-13, FR-15.11) --------------------------------------

UNVERIFIED_KEY: Final = "Tumnis could not verify this key is read-only"
CapabilitySource = Literal["b2_authorize_account", "minio_account_info", "none"]
S3Change = Literal["new", "changed", "unchanged", "deleted"]
_B2_MASTER_KEY_ID: Final = re.compile(r"^[0-9a-f]{12}$")
_ARN_S3: Final = "arn:aws:s3:::"
_PROBE_OBJECT: Final = ".tumnis-capability-probe"


class KeyCapabilities(BaseModel, frozen=True):
    """What a linked source's key may do, as far as its provider lets Tumnis check
    (`checked` False, every field None, when it does not)."""

    checked: bool
    can_read: bool | None = None
    can_list: bool | None = None
    can_write: bool | None = None
    can_delete: bool | None = None
    bucket_scoped: bool | None = None
    prefix: str | None = None
    source: CapabilitySource = "none"


UNCHECKED: Final = KeyCapabilities(checked=False)


def capabilities_acceptable(c: KeyCapabilities) -> tuple[bool, str | None]:
    """(accepted, reason or warning). A checked key that can write (checked first), can
    delete or is not limited to the bucket is refused with `key_can_write`,
    `key_can_delete` or `key_not_scoped`; a checked read-only scoped key is accepted with
    nothing to say; an unchecked key is accepted with the visible UNVERIFIED_KEY warning."""
    if not c.checked:
        return True, UNVERIFIED_KEY
    if c.can_write:
        return False, "key_can_write"
    if c.can_delete:
        return False, "key_can_delete"
    if not c.bucket_scoped:
        return False, "key_not_scoped"
    return True, None


def b2_key_capabilities(allowed: Mapping[str, object], bucket: str) -> KeyCapabilities:
    """`apiInfo.storageApi.allowed` of a `b2_authorize_account` answer (API v4) for a key
    meant for `bucket`: any `write*` or `delete*` capability counts (files, buckets,
    retention, ...); scoped only when the key is restricted to that one bucket."""
    raw_caps = allowed.get("capabilities")
    caps = {str(c) for c in raw_caps} if isinstance(raw_caps, list) else set()
    buckets = allowed.get("buckets")
    names = (
        {b.get("name") if isinstance(b, Mapping) else None for b in buckets}
        if isinstance(buckets, list)
        else set()
    )
    prefix = allowed.get("namePrefix")
    return KeyCapabilities(
        checked=True,
        can_read="readFiles" in caps,
        can_list="listFiles" in caps,
        can_write=any(c.startswith("write") for c in caps),
        can_delete=any(c.startswith("delete") for c in caps),
        bucket_scoped=names == {bucket},
        prefix=prefix if isinstance(prefix, str) and prefix else None,
        source="b2_authorize_account",
    )


def _wildcard(pattern: str) -> re.Pattern[str]:
    """An IAM pattern (`*` any run, `?` one character) as a whole-string regex."""
    parts = (".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in pattern)
    return re.compile("^" + "".join(parts) + "$", re.IGNORECASE)


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _allows(statement: Mapping[str, object], action: str, resource: str) -> bool:
    """Whether an Allow statement grants `action` on `resource`. `NotAction` and
    `NotResource` count as granting everything they do not name (the cautious reading)."""
    if "NotAction" in statement:
        action_ok = not any(_wildcard(p).match(action) for p in _strings(statement["NotAction"]))
    else:
        action_ok = any(_wildcard(p).match(action) for p in _strings(statement.get("Action")))
    if "NotResource" in statement:
        listed = _strings(statement["NotResource"])
        resource_ok = not any(_wildcard(p).match(resource) for p in listed)
    else:
        resource_ok = any(_wildcard(p).match(resource) for p in _strings(statement.get("Resource")))
    return action_ok and resource_ok


def _scoped_resource(pattern: str, bucket: str) -> bool:
    """A resource naming only `bucket` (or objects in it): no wildcard in the bucket part."""
    if not pattern.startswith(_ARN_S3):
        return False
    name, _, _ = pattern[len(_ARN_S3) :].partition("/")
    return name == bucket


_READ_ACTION_PREFIXES: Final = ("s3:get", "s3:list")  # a pattern starting so only reads
_DELETE_ACTIONS: Final = ("s3:DeleteObject", "s3:DeleteObjectVersion", "s3:DeleteBucket")


def _s3_action(pattern: str) -> bool:
    return pattern == "*" or pattern.lower().startswith("s3:")


def _only_reads(pattern: str) -> bool:
    """Whether an action pattern can match only Get/List actions: its fixed start is
    `s3:Get` or `s3:List` (a wildcard earlier than that could match anything)."""
    fixed = re.split(r"[*?]", pattern, maxsplit=1)[0].lower()
    return any(fixed.startswith(p) for p in _READ_ACTION_PREFIXES)


def _only_deletes(pattern: str) -> bool:
    """Whether an action pattern can match only Delete actions (its fixed start)."""
    return re.split(r"[*?]", pattern, maxsplit=1)[0].lower().startswith("s3:delete")


def _deletes(pattern: str) -> bool:
    return pattern.lower().startswith("s3:delete") or any(
        _wildcard(pattern).match(a) for a in _DELETE_ACTIONS
    )


def _reaches_under(pattern: str, base: str) -> bool:
    """Whether an IAM pattern (`*` any run, `?` one character) matches some `base + s`
    with `s` not empty: the pattern is run over `base` as a set of positions, and any
    position short of its end can still take more characters."""
    p = pattern.lower()

    def stars(states: set[int]) -> set[int]:  # a `*` may also match nothing
        out, todo = set(states), list(states)
        while todo:
            i = todo.pop()
            if i < len(p) and p[i] == "*" and i + 1 not in out:
                out.add(i + 1)
                todo.append(i + 1)
        return out

    states = stars({0})
    for ch in base.lower():
        states = stars(
            {i if p[i] == "*" else i + 1 for i in states if i < len(p) and p[i] in ("*", "?", ch)}
        )
        if not states:
            return False
    return any(i < len(p) for i in states)


def _overlaps(pattern: str, bucket_arn: str, bases: Sequence[str]) -> bool:
    """Whether a Resource pattern can name the bucket itself or an object under one of
    `bases` (`<bucket arn>/<prefix>`): a narrower grant (`.../acme/sub/*`) counts, and so
    does a wider one a wildcard reaches into (`.../ac*`); `.../acme?` does not (it names
    only `acme` plus one character). Compared without case, so it only ever errs towards
    overlapping."""
    return _wildcard(pattern).match(bucket_arn) is not None or any(
        _reaches_under(pattern, b) for b in bases
    )


def _on_any(statement: Mapping[str, object], bucket_arn: str, bases: Sequence[str]) -> bool:
    """Whether a statement's resources can reach the bucket or the mapped prefixes;
    `NotResource` always can (the cautious reading)."""
    if "NotResource" in statement:
        return True
    return any(_overlaps(p, bucket_arn, bases) for p in _strings(statement.get("Resource")))


def minio_key_capabilities(
    policy: Mapping[str, object] | None, bucket: str, prefixes: Sequence[str]
) -> KeyCapabilities:
    """The key's own policy, as MinIO's account info reports it, for objects under each of
    `prefixes` in `bucket`. Conditions are ignored and Deny statements are not relied on,
    so the answer only ever errs towards refusing: any `admin:` action counts (it can
    change policies), and so does any S3 action that is not a Get or List on a resource
    that can reach the bucket itself or anything under those prefixes (a narrower
    sub-prefix too): a delete as `can_delete`, anything else (an object write, a lifecycle
    rule, a bucket policy, versioning) as `can_write`."""
    raw = (policy or {}).get("Statement")
    statements = [s for s in (raw if isinstance(raw, list) else []) if isinstance(s, Mapping)]
    allow = [s for s in statements if str(s.get("Effect", "")).lower() == "allow"]
    bucket_arn = f"{_ARN_S3}{bucket}"
    objects = [f"{bucket_arn}/{p}{_PROBE_OBJECT}" for p in (prefixes or [""])]

    def granted(action: str, resources: Sequence[str]) -> bool:
        return any(_allows(s, action, r) for s in allow for r in resources)

    admin = any(
        _wildcard(p).match("admin:SetPolicy") for s in allow for p in _strings(s.get("Action"))
    ) or any("NotAction" in s for s in allow)
    scoped = all(
        "NotResource" not in s
        and all(_scoped_resource(r, bucket) for r in _strings(s.get("Resource")))
        for s in allow
        if any(a.lower().startswith("s3:") or a == "*" for a in _strings(s.get("Action")))
        or "NotAction" in s
    )
    bases = [f"{bucket_arn}/{p}" for p in (prefixes or [""])]
    beyond_reads = [
        pattern
        for s in allow
        if _on_any(s, bucket_arn, bases)
        for pattern in _strings(s.get("Action"))
        if _s3_action(pattern) and not _only_reads(pattern)
    ]
    return KeyCapabilities(
        checked=True,
        can_read=granted("s3:GetObject", objects),
        can_list=granted("s3:ListBucket", [bucket_arn]),
        can_write=admin or any(not _only_deletes(p) for p in beyond_reads),
        can_delete=admin or any(_deletes(p) for p in beyond_reads),
        bucket_scoped=scoped and bool(allow),
        prefix=None,
        source="minio_account_info",
    )


def looks_like_b2_master_key_id(key_id: str) -> bool:
    """A B2 master application key's id is the account id, 12 hex characters (plan
    default heuristic); application keys are longer. Tumnis never takes the master key."""
    return bool(_B2_MASTER_KEY_ID.match(key_id.strip()))


def normalize_prefix(prefix: str) -> str:
    """A bucket prefix as a folder: no leading `/`, one trailing `/` (`acme` -> `acme/`), so
    `acme/` never matches `acme-old/...`; empty stays empty (the whole bucket)."""
    p = prefix.strip().lstrip("/")
    return p if not p or p.endswith("/") else p + "/"


def prefix_for(key: str, prefixes: Iterable[str]) -> str | None:
    """The longest mapped prefix `key` lies under; None outside every prefix, and for a
    folder placeholder (a key ending in `/`)."""
    if key.endswith("/"):
        return None
    under = [p for p in prefixes if key.startswith(p) and len(key) > len(p)]
    return max(under, key=len) if under else None


class FolderFileLite(BaseModel, frozen=True):
    """What the last sync recorded for an object (its `folder_files` row)."""

    etag: str
    size: int
    mtime: datetime


class S3ObjectLite(BaseModel, frozen=True):
    """One object as `ListObjectsV2` (or a HEAD) reports it."""

    key: str
    etag: str
    size: int
    last_modified: datetime


def s3_change(prev: FolderFileLite | None, obj: S3ObjectLite | None) -> S3Change:
    """new (not seen before), deleted (seen, now gone), changed (another ETag, or the same
    ETag with a newer time and another size: a multipart ETag is no content hash), else
    unchanged. Both None is a caller error."""
    if obj is None:
        if prev is None:
            raise ValueError("s3_change needs a previous record or a listed object")
        return "deleted"
    if prev is None:
        return "new"
    if not etag_equal(prev.etag, obj.etag):
        return "changed"
    if obj.last_modified > prev.mtime and obj.size != prev.size:
        return "changed"
    return "unchanged"


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


# --- Trust and passages (P1-17, FR-15.4, FR-15.5) -----------------------------------------

Origin = Literal["user_text", "upload", "folder_external", "agent", "link"]
Trust = Literal["trusted", "untrusted"]
PASSAGE_CAP_CHARS: Final = 6_000  # plan default for the packet's knowledge part


class Passage(BaseModel):
    """A piece of knowledge for a packet: the brief (no chunk) or a chunk, with its
    citation (document, title, heading path, page)."""

    model_config = ConfigDict(frozen=True)

    chunk_id: UUID | None
    document_id: UUID
    title: str
    heading_path: list[str]
    page: int | None
    text: str
    tainted: bool = False


_TRUST: Final[Mapping[str, tuple[Trust, bool]]] = {
    "user_text": ("trusted", False),
    "upload": ("untrusted", True),
    "folder_external": ("untrusted", True),
    "agent": ("untrusted", False),
    "link": ("trusted", False),
}


def default_trust(origin: Origin) -> tuple[Trust, bool]:
    """(trust, tainted): user_text -> (trusted, False); upload and folder_external ->
    (untrusted, True) (FR-15.5, SAF-1); agent -> (untrusted, False) until reviewed; link ->
    (trusted, False), content not fetched."""
    return _TRUST[origin]


BRIEF_TRUNCATED: Final = "[brief truncated]"


def select_passages(
    brief: Passage | None, ranked: Sequence[Passage], cap: int = PASSAGE_CAP_CHARS
) -> list[Passage]:
    """Brief first, cut to cap // 2 with a '[brief truncated]' marker if longer; then ranked
    passages in order, skipping the brief's document and exact duplicates, stopping at the
    first passage that would exceed the cap (no partial passages)."""
    out: list[Passage] = []
    used = 0
    if brief is not None:
        half = cap // 2
        text = brief.text
        if len(text) > half:
            keep = half - len(BRIEF_TRUNCATED) - 1
            text = text[:keep] + "\n" + BRIEF_TRUNCATED if keep >= 0 else text[:half]
        out.append(brief.model_copy(update={"text": text}))
        used = len(text)
    seen: set[str] = set()
    for passage in ranked:
        if brief is not None and passage.document_id == brief.document_id:
            continue
        if passage.text in seen:
            continue
        if used + len(passage.text) > cap:
            break
        seen.add(passage.text)
        out.append(passage)
        used += len(passage.text)
    return out


MAX_QUERY_TERMS: Final = 12
_WORD: Final = re.compile(r"[^\W\d_]+")
_STOP_WORDS: Final = frozenset(
    """a about above after again against all also am an and any are as at be because been
    before being below between both but by can could did do does doing down during each few
    for from further get got had has have having he her here hers him his how if in into is
    it its just let me more most must my no nor not now of off on once only or other our
    ours out over own same she should so some such than that the their them then there these
    they this those through to too under until up upon very via was we were what when where
    which while who whom why will with would you your yours""".split()  # noqa: SIM905
)


def passage_query(title: str, criteria: Sequence[str], goal: str | None) -> str:
    """Up to 12 distinct terms of 3+ letters, stop words removed, joined with ' or ' for
    websearch_to_tsquery (which reads the word `or` as OR)."""
    terms: list[str] = []
    for text in (title, *criteria, goal or ""):
        for word in _WORD.findall(text.casefold()):
            if len(word) >= 3 and word not in _STOP_WORDS and word not in terms:  # noqa: PLR2004
                terms.append(word)
                if len(terms) == MAX_QUERY_TERMS:
                    return " or ".join(terms)
    return " or ".join(terms)


# --- Text entries cut into chunks by their Markdown headings (P1-17, FR-15.3) -------------

CHUNK_MAX_CHARS: Final = 2_000  # plan default: about Docling's 512 tokens
_HEADING: Final = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$")
_FENCE: Final = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")  # CommonMark: at most 3 spaces in
_FRONTMATTER: Final = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def markdown_sections(markdown: str) -> list[tuple[list[str], str]]:
    """A text entry's Markdown as (heading path, text) sections: ATX headings (outside
    fenced code) open a section under their parents; leading frontmatter is dropped; a
    section's text is its body without the heading line, cut at blank lines into pieces of
    at most CHUNK_MAX_CHARS (a longer paragraph is cut hard). Sections with no text are
    left out; a heading with nothing under it still makes its path searchable through the
    next section's path."""
    body = _FRONTMATTER.sub("", markdown.replace("\r\n", "\n"), count=1)
    path: list[str] = []
    sections: list[tuple[list[str], list[str]]] = [([], [])]
    fence: str | None = None  # the open code fence's marker
    for line in body.split("\n"):
        fence = _fence_after(line, fence)
        heading = None if fence is not None else _HEADING.match(line)
        if heading is None:
            sections[-1][1].append(line)
            continue
        level = len(heading.group(1))
        path = [*path[: level - 1], heading.group(2).strip()]
        sections.append((path, []))
    out: list[tuple[list[str], str]] = []
    for heading_path, lines in sections:
        for piece in _pieces("\n".join(lines).strip()):
            out.append((heading_path, piece))
    return out


def _fence_after(line: str, fence: str | None) -> str | None:
    """The open code fence after `line` (CommonMark): a fence opens with 3 or more
    backticks or tildes (a backtick fence's info string has no backtick) and closes only
    with the same character, at least as long, and nothing but spaces after it."""
    found = _FENCE.match(line)
    if found is None:
        return fence
    marker, rest = found.group(1), found.group(2)
    if fence is None:
        return None if marker[0] == "`" and "`" in rest else marker
    if marker[0] == fence[0] and len(marker) >= len(fence) and not rest.strip():
        return None
    return fence


def _pieces(text: str) -> list[str]:
    """`text` cut at blank lines into pieces of at most CHUNK_MAX_CHARS."""
    if not text:
        return []
    pieces: list[str] = []
    current = ""
    for block in re.split(r"\n\s*\n", text):
        para = block
        while len(para) > CHUNK_MAX_CHARS:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(para[:CHUNK_MAX_CHARS])
            para = para[CHUNK_MAX_CHARS:]
        joined = f"{current}\n\n{para}" if current else para
        if len(joined) > CHUNK_MAX_CHARS:
            pieces.append(current)
            current = para
        else:
            current = joined
    if current.strip():
        pieces.append(current)
    return pieces


# --- Hybrid search (P3-10, FR-15.3, FR-11.10) ----------------------------------------------

RRF_K: Final = 60  # plan default: the constant of the original RRF paper (Cormack et al. 2009)
CANDIDATES: Final = 50  # per list, plan default
EMBED_BATCH: Final = 64  # chunks per re-embed step, plan default
EMBED_QUEUE: Final = "embed"  # the worker's queue for re-embedding (worker concurrency 2)
REEMBED_WORKFLOW: Final = "knowledge_reembed_all"
DEFAULT_EMBEDDING_MODEL: Final = "BAAI/bge-m3"  # plan default (local vLLM)
DEFAULT_EMBEDDING_DIMS: Final = 1024
HNSW_MAX_DIMS: Final = 2000  # pgvector: HNSW indexes `vector` up to 2,000 dimensions
# Cosine distance: the index's operator class and the query's operator, kept together so
# they cannot drift (the planner uses the index only when both match).
COSINE_OPCLASS: Final = "vector_cosine_ops"
COSINE_OPERATOR: Final = "<=>"
HNSW_PREFIX: Final = "embeddings_hnsw_"
_PG_IDENTIFIER_MAX: Final = 63
_MODEL_NAME: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$")
_SLUG_JUNK: Final = re.compile(r"[^a-z0-9]+")
Source = Literal["fulltext", "vector"]


@dataclass(frozen=True)
class Ranked:
    chunk_id: UUID
    rank: int  # 1-based within its list


@dataclass(frozen=True)
class FusedHit:
    """One fused result: the chunk, its RRF score, which lists found it and its best rank in
    either. Search hydrates the citation fields (document, heading path, page) afterwards."""

    chunk_id: UUID
    score: float
    sources: frozenset[Source]
    best_rank: int


def rrf_merge(
    fulltext: Sequence[Ranked], vector: Sequence[Ranked], k: int = RRF_K, limit: int = 10
) -> list[FusedHit]:
    """score(c) = sum over lists containing c of 1 / (k + rank). Ties break by better
    best-rank, then chunk_id. Raw scores are ignored: only ranks are comparable."""
    scores: dict[UUID, float] = {}
    sources: dict[UUID, set[Source]] = {}
    best: dict[UUID, int] = {}
    lists: tuple[tuple[Source, Sequence[Ranked]], ...] = (
        ("fulltext", fulltext),
        ("vector", vector),
    )
    for source, ranked in lists:
        for item in ranked:
            found = sources.setdefault(item.chunk_id, set())
            if source in found:
                continue  # a list names a chunk once; a repeat adds nothing
            found.add(source)
            scores[item.chunk_id] = scores.get(item.chunk_id, 0.0) + 1 / (k + item.rank)
            best[item.chunk_id] = min(best.get(item.chunk_id, item.rank), item.rank)
    order = sorted(scores, key=lambda c: (-scores[c], best[c], c))
    return [FusedHit(c, scores[c], frozenset(sources[c]), best[c]) for c in order[:limit]]


def recall_at_k(
    results: Mapping[str, Sequence[UUID]], expected: Mapping[str, set[UUID]], k: int = 5
) -> float:
    """Mean over queries of |expected ∩ top_k| / min(k, |expected|); a query with nothing
    expected is skipped, and no query at all is 0.0."""
    recalls = [
        len(want & set(list(results.get(query, []))[:k])) / min(k, len(want))
        for query, want in expected.items()
        if want
    ]
    return sum(recalls) / len(recalls) if recalls else 0.0


def valid_model_name(model: str) -> bool:
    """A model name Tumnis accepts: it is written into SQL as a literal (the partial index's
    predicate), so only letters, digits and `._:/@+-`, at most 200 characters."""
    return bool(_MODEL_NAME.match(model))


def model_slug(model: str) -> str:
    """`BAAI/bge-m3` -> `baai_bge_m3`."""
    return _SLUG_JUNK.sub("_", model.lower()).strip("_")


def _fnv1a(text: str) -> str:
    """32-bit FNV-1a as 8 hex digits (pure arithmetic: rules import no hashlib)."""
    h = 0x811C9DC5
    for byte in text.encode():
        h = ((h ^ byte) * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def hnsw_index_name(model: str) -> str:
    """The model's partial HNSW index: `embeddings_hnsw_<slug>`, within PostgreSQL's 63-byte
    identifier limit (a longer one is cut and ends in a hash of the model name)."""
    name = HNSW_PREFIX + model_slug(model)
    if len(name) <= _PG_IDENTIFIER_MAX:
        return name
    suffix = "_" + _fnv1a(model)
    return name[: _PG_IDENTIFIER_MAX - len(suffix)] + suffix


# --- Existing folders (P3-14, FR-15.12) ------------------------------------------------


class ActorKind(StrEnum):
    user = "user"
    agent = "agent"
    system = "system"


@dataclass(frozen=True)
class WritePolicy:
    mode: Literal["tumnis_made", "existing"]
    tumnis_subdir: str = "Tumnis/"  # FR-15.12

    def __post_init__(self) -> None:
        """`may_write` uses `tumnis_subdir` as a path prefix, so it must be a safe relative
        folder that ends in '/' (else `TumnisX/a.md` would pass for `Tumnis`)."""
        sub = self.tumnis_subdir
        try:
            safe = sub.endswith("/") and safe_rel_path(sub[:-1]) == sub[:-1]
        except PathRejected:
            safe = False
        if not safe:
            raise ValueError(f"tumnis_subdir must be a safe folder ending in '/': {sub!r}")


def may_write(p: WritePolicy, path: str, origin: Literal["tumnis", "external"] | None) -> bool:
    """existing mode: only paths under Tumnis/, and never over a file whose origin is
    external. tumnis_made: anywhere in the root, never over an external file. A path that
    `safe_rel_path` refuses or would change is never written."""
    if origin == "external":
        return False
    try:
        if safe_rel_path(path) != path:
            return False
    except PathRejected:
        return False
    if p.mode == "tumnis_made":
        return True
    return path.startswith(p.tumnis_subdir) and len(path) > len(p.tumnis_subdir)


def may_rename(p: WritePolicy, src_origin: str) -> bool:
    """External files: never (a rename in the app changes the title only)."""
    return src_origin != "external"


def may_delete(
    p: WritePolicy, origin: str, actor: ActorKind, confirmed_by_user: bool
) -> Literal["trash", "delete_at_source", "index_only", "refuse"]:
    """tumnis origin: trash. external origin: agent -> refuse; otherwise index_only without
    the user's confirmation, delete_at_source with it."""
    if origin != "external":
        return "trash"
    if actor is ActorKind.agent:
        return "refuse"
    return "delete_at_source" if confirmed_by_user else "index_only"


def trash_dir(p: WritePolicy) -> str:
    """`.tumnis/trash/` in a Tumnis-made folder; `Tumnis/.trash/` in an existing one."""
    return ".tumnis/trash/" if p.mode == "tumnis_made" else f"{p.tumnis_subdir}.trash/"
