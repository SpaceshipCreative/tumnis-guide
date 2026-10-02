"""The Obsidian vault's pure rules (P3-12, FR-15.10, SAF-1): no I/O.

- `map_note_to_project`: which project a note belongs to. Precedence (plan default): the
  frontmatter key, then a `#tumnis/<project>` tag, then the longest matching folder
  prefix. A project nobody knows is skipped at its level; with none at any level the note
  goes to the unmapped handling (the workspace knowledge base, or ignored).
- `is_excluded`: `.obsidian/`, `.trash/`, the templates folder and the user's excludes are
  never read.
- `note_trust`: notes under the clippings folder come from the web: untrusted.
- `resolve_link`: Obsidian's shortest-path rule (a unique basename, else the path).

Projects have no slug column: a project is named in the vault by `project_slug` of its
name (case and spacing ignored).
"""

import json
import posixpath
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

from tumnis.modules.knowledge.obsidian.parse import ParsedNote
from tumnis.modules.knowledge.rules import Trust

__all__ = [
    "ALWAYS_EXCLUDED",
    "DEFAULT_TEMPLATES",
    "TEMPLATES_CONFIG",
    "VaultMapping",
    "is_excluded",
    "is_note_path",
    "map_note_to_project",
    "note_trust",
    "project_slug",
    "resolve_link",
    "templates_folder",
]

ALWAYS_EXCLUDED: Final = (".obsidian", ".trash", ".git")
DEFAULT_TEMPLATES: Final = "Templates"
TEMPLATES_CONFIG: Final = ".obsidian/templates.json"
_NOT_SLUG: Final = re.compile(r"[^\w]+")


@dataclass(frozen=True)
class VaultMapping:
    folders: Mapping[str, str] = field(default_factory=dict)  # "Clients/Acme" -> project
    frontmatter_key: str = "tumnis_project"
    tag_prefix: str = "tumnis/"
    unmapped: Literal["workspace", "ignore"] = "workspace"
    clippings_folder: str = "Clippings"
    extra_excludes: tuple[str, ...] = ()


def project_slug(name: str) -> str:
    """A project's name as the vault names it: lowercased, runs of anything but letters,
    digits and `_` turned into one `-`, trimmed."""
    return _NOT_SLUG.sub("-", name.strip().lower()).strip("-")


def _folder(raw: str) -> str:
    """A folder setting as a vault path: no leading or trailing slashes."""
    return raw.strip().strip("/")


def _under(path: str, folder: str) -> bool:
    """`path` is `folder` itself or inside it (whole path segments)."""
    folder = _folder(folder)
    return bool(folder) and (path == folder or path.startswith(folder + "/"))


def _known(raw: object, known_projects: Collection[str]) -> str | None:
    if not isinstance(raw, str):
        return None
    slug = project_slug(raw)
    return slug if slug and slug in known_projects else None


def map_note_to_project(
    note: ParsedNote, m: VaultMapping, known_projects: Collection[str]
) -> str | Literal["ignore"] | None:
    """The note's project slug; None for the workspace knowledge base, "ignore" to skip
    the note (both only when no level names a known project)."""
    found = _known(note.frontmatter.get(m.frontmatter_key), known_projects)
    if found is not None:
        return found
    prefix = m.tag_prefix.lower()
    for tag in sorted(note.tags):
        if prefix and tag.startswith(prefix):
            found = _known(tag[len(prefix) :], known_projects)
            if found is not None:
                return found
    for folder, project in sorted(m.folders.items(), key=lambda kv: -len(_folder(kv[0]))):
        if _under(note.path, folder):
            found = _known(project, known_projects)
            if found is not None:
                return found
    return None if m.unmapped == "workspace" else "ignore"


def templates_folder(config_json: str | None) -> str:
    """The templates folder `.obsidian/templates.json` names (its `folder`), else
    `Templates`."""
    if config_json is None:
        return DEFAULT_TEMPLATES
    try:
        data = json.loads(config_json)
    except ValueError:
        return DEFAULT_TEMPLATES
    folder = data.get("folder") if isinstance(data, dict) else None
    if not isinstance(folder, str) or not _folder(folder):
        return DEFAULT_TEMPLATES
    return _folder(folder)


def is_excluded(path: str, m: VaultMapping, templates: str = DEFAULT_TEMPLATES) -> bool:
    """`path` (a file or a folder) is never read: `.obsidian/`, `.trash/`, the clone's
    `.git/`, the templates folder and the user's excludes, with everything under them."""
    folders = (*ALWAYS_EXCLUDED, templates, *m.extra_excludes)
    return any(_under(path, folder) for folder in folders)


def note_trust(path: str, m: VaultMapping) -> Trust:
    """Untrusted under the clippings folder (web clippings, SAF-1), else trusted."""
    return "untrusted" if _under(path, m.clippings_folder) else "trusted"


def is_note_path(path: str) -> bool:
    return path.lower().endswith(".md")


def _strip_md(path: str) -> str:
    return path[:-3] if is_note_path(path) else path


def resolve_link(target: str, from_path: str, paths: Collection[str]) -> str | None:
    """The vault path a link's target names, by Obsidian's rule: a path written in full
    (with or without `.md`), else a path relative to the linking note's folder, else the
    unique file with that basename. None when nothing or several files match."""
    target = target.strip().strip("/")
    if not target:
        return None
    by_key: dict[str, str] = {}
    for path in paths:
        by_key.setdefault(_strip_md(path).casefold(), path)
    key = _strip_md(target).casefold()
    if key in by_key:
        return by_key[key]
    if "/" in target:
        relative = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), target))
        if not relative.startswith("../") and _strip_md(relative).casefold() in by_key:
            return by_key[_strip_md(relative).casefold()]
        suffix = "/" + key
        tails = [p for p in paths if _strip_md(p).casefold().endswith(suffix)]
        return tails[0] if len(tails) == 1 else None
    named = [p for p in paths if posixpath.basename(_strip_md(p)).casefold() == key]
    return named[0] if len(named) == 1 else None
