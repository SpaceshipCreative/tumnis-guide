"""Shared fixtures (A5), loaded for the whole rootdir by backend/conftest.py.

Core and module tests live under tumnis/**/tests (R-16), outside backend/tests, so the
shared fixtures live in this plugin rather than in backend/tests/conftest.py.
"""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Integration tests never forget to open the socket block."""
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(pytest.mark.enable_socket)
