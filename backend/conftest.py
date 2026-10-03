"""Root conftest: loads the shared fixtures for backend/tests and backend/tumnis/**/tests.

pytest applies a conftest only to its own folder, and core and module tests live under
tumnis/ (R-16), so the shared fixtures are a plugin loaded here for the whole rootdir.
"""

import importlib.util
import os
from pathlib import Path

import pytest

# Typer forces a Rich terminal (ANSI styles in `--help`) when GITHUB_ACTIONS, FORCE_COLOR or
# PY_COLORS is set, so CLI output would differ between CI and a laptop. Tests read plain
# text; this is Typer's own switch for its test suite, read when typer.rich_utils loads.
os.environ["_TYPER_FORCE_DISABLE_TERMINAL"] = "1"

pytest_plugins = ["pytester", "tests.fixtures", "tests._services", "tests.fakes.fake_runner"]

BACKEND = Path(__file__).resolve().parent

# Tests that run the real Docling pipeline (Scott decision 85): file under backend/ to test
# function names. Docling is the optional `docling` dependency group, which only the `docling` CI
# job (.github/workflows/docling.yml) installs. The list lives here because a marker added
# in a locked test file counts as an edit (spec-guard). Each listed test gets the `docling`
# marker, which that job selects, and is skipped wherever Docling is not installed (the
# integration jobs, make test-int).
REAL_DOCLING_TESTS: dict[str, frozenset[str]] = {
    "tests/acceptance/test_a1_5_pdf_to_packet.py": frozenset(
        {
            "test_pdf_is_scanned_extracted_and_filed",
            "test_table_chunk_is_searchable_with_page",
            "test_task_packet_carries_brief_and_table_passage",
        }
    ),
    "tumnis/modules/knowledge/tests/integration/test_extraction_set.py": frozenset(
        {"test_expected_chunks"}
    ),
    "tumnis/modules/knowledge/tests/unit/test_docling_memory.py": frozenset(
        {"test_converter_keeps_few_pages_in_flight"}
    ),
}
NO_DOCLING = pytest.mark.skip(
    reason="needs Docling: `uv sync --group docling` (the docling CI job, Scott decision 85)"
)


@pytest.hookimpl(tryfirst=True)  # before -m and -k deselect anything
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark the real-Docling tests `docling`, and skip them when Docling is not installed.

    A listed test missing from its collected module is a usage error, so a renamed test
    cannot fall out of the docling job while staying skipped everywhere else."""
    installed = importlib.util.find_spec("docling") is not None
    for item in items:
        try:
            file = item.path.resolve().relative_to(BACKEND).as_posix()
        except ValueError:  # pytester's own temporary test files
            continue
        listed = REAL_DOCLING_TESTS.get(file)
        if listed is None:
            continue
        missing = sorted(n for n in listed if not hasattr(getattr(item, "module", None), n))
        if missing:
            raise pytest.UsageError(f"REAL_DOCLING_TESTS: {file} has no {', '.join(missing)}")
        if getattr(item, "originalname", item.name) in listed:
            item.add_marker(pytest.mark.docling)
            if not installed:
                item.add_marker(NO_DOCLING)
