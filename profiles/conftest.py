"""pytest for the profiles project: the harness unit tests (any machine), and the skill
cases under tests/cases as items that skip unless `--run-skills` is given."""

pytest_plugins = ["harness.pytest_plugin"]
