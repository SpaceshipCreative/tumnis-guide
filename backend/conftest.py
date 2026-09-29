"""Root conftest: loads the shared fixtures for backend/tests and backend/tumnis/**/tests.

pytest applies a conftest only to its own folder, and core and module tests live under
tumnis/ (R-16), so the shared fixtures are a plugin loaded here for the whole rootdir.
"""

pytest_plugins = ["pytester"]
