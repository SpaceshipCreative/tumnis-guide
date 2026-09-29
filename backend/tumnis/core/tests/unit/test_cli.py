"""The image's commands (P0-04, FR-12.4)."""

from __future__ import annotations

import re

import pytest


@pytest.mark.req("FR-12.4")
@pytest.mark.wp("P0-04")
def test_image_commands_exist() -> None:
    """T-P0-04-11
    `tumnis --help` lists api, worker, migrate and seed.
    """
    from typer.testing import CliRunner  # noqa: PLC0415

    from tumnis.cli import app  # noqa: PLC0415

    result = CliRunner().invoke(app, ["--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    for command in ("api", "worker", "migrate", "seed"):
        assert re.search(rf"^\W*{command}\b", result.output, re.MULTILINE), command
