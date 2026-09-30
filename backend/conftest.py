"""Root conftest: loads the shared fixtures for backend/tests and backend/tumnis/**/tests.

pytest applies a conftest only to its own folder, and core and module tests live under
tumnis/ (R-16), so the shared fixtures are a plugin loaded here for the whole rootdir.
"""

import os

# Typer forces a Rich terminal (ANSI styles in `--help`) when GITHUB_ACTIONS, FORCE_COLOR or
# PY_COLORS is set, so CLI output would differ between CI and a laptop. Tests read plain
# text; this is Typer's own switch for its test suite, read when typer.rich_utils loads.
os.environ["_TYPER_FORCE_DISABLE_TERMINAL"] = "1"

pytest_plugins = ["pytester", "tests.fixtures", "tests._services", "tests.fakes.fake_runner"]
