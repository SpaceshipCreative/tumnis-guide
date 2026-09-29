"""Fixtures for knowledge's tests (P1-14): `tmp_location`, a temp root with the marker."""

from __future__ import annotations

from pathlib import Path

import pytest

MARKER = ".tumnis-root"  # ServerPathStorage.MARKER


@pytest.fixture
def tmp_location(tmp_path: Path) -> Path:
    """An empty server-path location root holding its `.tumnis-root` marker."""
    root = tmp_path / "location"
    root.mkdir()
    (root / MARKER).write_text("tumnis\n")
    return root
