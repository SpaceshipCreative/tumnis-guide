"""The Obsidian vault rules (P3-12, FR-15.10, SAF-1): which project a note maps to, which
paths are never read, and which notes are tainted. Pure functions, no I/O."""

from __future__ import annotations

import importlib
from typing import Any

import pytest

KNOWN = {"acme", "lab", "beta-launch"}


def _note(path: str, text: str) -> Any:
    _parse = importlib.import_module("tumnis.modules.knowledge.obsidian.parse")
    parse_note = _parse.parse_note

    return parse_note(path, text)


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
def test_mapping_precedence() -> None:
    """T-P3-12-04
    The frontmatter key beats the `#tumnis/<project>` tag, which beats the longest
    matching folder prefix. A project name nobody knows is skipped at its level; with no
    known project at any level the note goes to the unmapped handling: the workspace
    knowledge base (None) by default, or "ignore". Names compare as slugs (case and
    spacing do not matter).
    """
    _rules = importlib.import_module("tumnis.modules.knowledge.obsidian.rules")
    VaultMapping = _rules.VaultMapping  # noqa: N806
    map_note_to_project = _rules.map_note_to_project
    project_slug = _rules.project_slug

    m = VaultMapping(
        folders={"Clients/Acme": "acme", "Clients": "lab", "Launches/Beta": "Beta Launch"}
    )

    def mapped(path: str, text: str, mapping: Any = m) -> object:
        return map_note_to_project(_note(path, text), mapping, KNOWN)

    everything = "---\ntumnis_project: lab\n---\n#tumnis/acme\n"
    assert mapped("Clients/Acme/All.md", everything) == "lab"  # frontmatter first
    assert mapped("Clients/Acme/Tag.md", "#tumnis/lab\n") == "lab"  # tag beats folder
    assert mapped("Clients/Acme/Folder.md", "plain\n") == "acme"  # longest prefix
    assert mapped("Clients/Other/Folder.md", "plain\n") == "lab"  # shorter prefix
    assert mapped("ClientsX/Folder.md", "plain\n") is None  # a prefix is whole segments
    assert mapped("Launches/Beta/Plan.md", "plain\n") == "beta-launch"
    assert mapped("Inbox/Note.md", "---\ntumnis_project: ACME\n---\n") == "acme"
    assert mapped("Inbox/Note.md", "---\ntumnis_project: Beta Launch\n---\n") == "beta-launch"
    assert mapped("Inbox/Note.md", "---\ntumnis_project: [acme]\n---\n") is None  # not a name

    # Unknown at one level: the next level decides; unknown everywhere: unmapped.
    assert mapped("Clients/Acme/Unknown.md", "---\ntumnis_project: nope\n---\n") == "acme"
    assert mapped("Inbox/Unknown.md", "---\ntumnis_project: nope\n---\n#tumnis/zzz\n") is None
    ignoring = VaultMapping(folders={}, unmapped="ignore")
    assert mapped("Inbox/Unknown.md", "#tumnis/zzz\n", ignoring) == "ignore"
    assert mapped("Inbox/Plain.md", "plain\n", ignoring) == "ignore"

    custom = VaultMapping(folders={}, frontmatter_key="project", tag_prefix="p/")
    assert mapped("Inbox/Custom.md", "---\nproject: acme\n---\n", custom) == "acme"
    assert mapped("Inbox/Custom.md", "#p/lab\n", custom) == "lab"
    assert mapped("Inbox/Custom.md", "---\ntumnis_project: acme\n---\n", custom) is None

    assert project_slug("  Beta  Launch ") == "beta-launch"
    assert project_slug("ACME") == "acme"


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
def test_excluded_folders_skipped() -> None:
    """T-P3-12-05
    `.obsidian/`, `.trash/`, the vault's templates folder (from `.obsidian/templates.json`
    when it names one, else `Templates/`) and the user's own excludes are excluded, the
    folders themselves and everything under them; other paths that merely start with the
    same letters are not.
    """
    _rules = importlib.import_module("tumnis.modules.knowledge.obsidian.rules")
    VaultMapping = _rules.VaultMapping  # noqa: N806
    is_excluded = _rules.is_excluded
    templates_folder = _rules.templates_folder

    paths = [
        ".obsidian",
        ".obsidian/app.json",
        ".obsidian/plugins/x/main.js",
        ".trash/Old.md",
        "Templates/Meeting.md",
        "Templates",
        "TemplatesArchive/Kept.md",
        "Private/Diary.md",
        "Private",
        "Clients/Acme/Kickoff.md",
        "Clients/Acme/Private/Kept.md",
        "Inbox/Rates.md",
    ]
    m = VaultMapping(folders={}, extra_excludes=("Private", "Archive/Old/"))
    default = templates_folder(None)
    assert default == "Templates"
    kept = [p for p in paths if not is_excluded(p, m, default)]
    assert kept == [
        "TemplatesArchive/Kept.md",
        "Clients/Acme/Kickoff.md",
        "Clients/Acme/Private/Kept.md",
        "Inbox/Rates.md",
    ]
    assert is_excluded("Archive/Old/a.md", m, default)
    assert not is_excluded("Archive/Older/a.md", m, default)

    custom = templates_folder('{"folder": "Meta/Templates"}')
    assert custom == "Meta/Templates"
    assert is_excluded("Meta/Templates/Daily.md", m, custom)
    assert not is_excluded("Templates/Meeting.md", m, custom)
    assert templates_folder("not json") == "Templates"
    assert templates_folder('{"folder": ""}') == "Templates"
    assert templates_folder('{"folder": "/Meta/T/"}') == "Meta/T"


@pytest.mark.req("FR-15.10", "SAF-1")
@pytest.mark.wp("P3-12")
def test_clippings_tainted_others_trusted() -> None:
    """T-P3-12-06
    A note under the clippings folder (`Clippings/` by default, configurable) is
    untrusted; everything else in the vault is trusted. Only the folder itself counts:
    `ClippingsOld/` and a nested `Clippings/` deeper in the vault are trusted.
    """
    _rules = importlib.import_module("tumnis.modules.knowledge.obsidian.rules")
    VaultMapping = _rules.VaultMapping  # noqa: N806
    note_trust = _rules.note_trust

    m = VaultMapping(folders={})
    assert note_trust("Clippings/Article.md", m) == "untrusted"
    assert note_trust("Clippings/Sub/Deep.md", m) == "untrusted"
    assert note_trust("Inbox/Rates.md", m) == "trusted"
    assert note_trust("ClippingsOld/Article.md", m) == "trusted"
    assert note_trust("Projects/Clippings/Article.md", m) == "trusted"

    web = VaultMapping(folders={}, clippings_folder="Web/Saved")
    assert note_trust("Web/Saved/Article.md", web) == "untrusted"
    assert note_trust("Clippings/Article.md", web) == "trusted"
