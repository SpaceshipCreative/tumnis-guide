"""github adapters: one file per outside dependency, each with a fake.

`github.status` is the read-only GitHub REST client (`github_status.py`, replayed by
`fake.py`). The real factory imports the client only when a real adapter is built, which
happens in the worker: the api process wires the registry but never imports it
(import-linter `api-never-calls-out`)."""

import importlib
from typing import Any

from tumnis.core.adapters.registry import register_adapter
from tumnis.modules.github.adapters.fake import FakeGitHubStatus
from tumnis.modules.github.adapters.port import GitHubStatus

STATUS_MODULE = "tumnis.modules.github.adapters.github_status"


def build_status(**deps: Any) -> GitHubStatus:
    """GitHubStatusApi(token=..., policy=..., clock=...), imported on first use."""
    status = importlib.import_module(STATUS_MODULE)
    api: GitHubStatus = status.GitHubStatusApi(**deps)
    return api


register_adapter("github.status", port=GitHubStatus, real=build_status, fake=FakeGitHubStatus)
