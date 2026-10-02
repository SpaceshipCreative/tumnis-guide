"""The worker's own startup configures what every process needs (APP-TEST-final finding 1).

Only `create_app` called `deadletter.configure`, so on the production image the worker had
no DBOS client: an S3 source sync took a file in, then failed to queue its extraction with
"tumnis.core.deadletter has no DBOS system database URL", and the document stayed
`pending_scan`. The tests that ran the S3 sync configured the client for the whole test
process, so none of them saw it. These run `tumnis.worker.main` itself, the function
`tumnis worker` calls, with DBOS stubbed (no connection) and the serve loop skipped, in a
process where nothing was configured first, and check the process-wide clients it leaves.
"""

from __future__ import annotations

import copy
import importlib
from typing import Any, ClassVar

import pytest

from tumnis import worker
from tumnis.core import cache, crypto, db, deadletter, faults, modules
from tumnis.settings import Settings

DSN = "postgresql+psycopg://tumnis_app:pw@127.0.0.1:5432/tumnis"


class StubDBOS:
    """DBOS without a database: records the config and does nothing else."""

    configs: ClassVar[list[dict[str, Any]]] = []
    destroyed: ClassVar[int] = 0

    def __init__(self, *, config: dict[str, Any]) -> None:
        StubDBOS.configs.append(config)

    @staticmethod
    def listen_queues(_queues: list[str]) -> None:
        return None

    @staticmethod
    def launch() -> None:
        return None

    @staticmethod
    def register_queue(_name: str, **_: Any) -> None:
        return None

    @staticmethod
    def apply_schedules(_schedules: list[dict[str, Any]]) -> None:
        return None

    @staticmethod
    def destroy() -> None:
        StubDBOS.destroyed += 1


def _module(name: str) -> Any:
    """Imported by name, as the worker does (no static edge from core's tests)."""
    return importlib.import_module(name)


# Every process-wide value worker.main sets: put back after the test (unit tests share a
# process under xdist). Dicts and state objects are copied, so the test mutates the copy.
_GLOBALS = (
    (deadletter, "_client"),
    (deadletter, "_client_owned"),
    (deadletter, "_client_url"),
    (db, "_state"),
    (cache, "_backend"),
    (modules, "_deployment_disabled"),
    (crypto, "_state"),
    (crypto, "_pepper_state"),
    (faults, "_armed"),
)
_MODULE_GLOBALS = (
    ("tumnis.modules.decisions.generation_config", "_slot"),
    ("tumnis.modules.decisions.embeddings_slot", "_slot"),
    ("tumnis.modules.decisions.speech_slot", "_slot"),
    ("tumnis.modules.decisions.api", "_net_policy"),
    ("tumnis.modules.agents.api", "_provisioning"),
    ("tumnis.modules.agents.api", "_limits"),
    ("tumnis.modules.agents.api", "_stuck_config"),
    ("tumnis.modules.integrations.workflows", "_net_policy"),
    ("tumnis.modules.integrations.workflows", "_resolved_oauth"),
    ("tumnis.modules.knowledge.pipeline", "_settings"),
    ("tumnis.modules.knowledge.pipeline", "_net"),
    ("tumnis.modules.knowledge.pipeline", "_defaults"),
)
_CONFIG_CLASSES = (
    ("tumnis.modules.knowledge.sync", ("net",)),
    ("tumnis.modules.knowledge.s3_sync", ("net", "clock")),
)


@pytest.fixture
def fresh_process(monkeypatch: pytest.MonkeyPatch) -> type[StubDBOS]:
    """A process where nothing is configured yet, DBOS stubbed and the serve loop skipped;
    every global the startup sets is restored afterwards."""
    import dbos  # noqa: PLC0415

    import tumnis.wiring  # noqa: F401, PLC0415  # the workflows register with the real DBOS

    for owner, name in _GLOBALS:
        monkeypatch.setattr(owner, name, copy.copy(getattr(owner, name)))
    for module_name, name in _MODULE_GLOBALS:
        owner = _module(module_name)
        monkeypatch.setattr(owner, name, copy.copy(getattr(owner, name)))
    for module_name, names in _CONFIG_CLASSES:
        config = _module(module_name)._Config
        for name in names:
            monkeypatch.setattr(config, name, getattr(config, name))
    # Nothing configured: as `tumnis worker` finds the process.
    monkeypatch.setattr(deadletter, "_client", None)
    monkeypatch.setattr(deadletter, "_client_owned", False)
    monkeypatch.setattr(deadletter, "_client_url", None)
    monkeypatch.setattr(db, "_state", db._State())
    monkeypatch.setattr(modules, "_deployment_disabled", frozenset())
    monkeypatch.delenv("TUMNIS_KILLPOINT", raising=False)

    async def no_serve(_settings: Settings, *, relay: bool = True) -> None:
        return None

    StubDBOS.configs = []
    StubDBOS.destroyed = 0
    monkeypatch.setattr(dbos, "DBOS", StubDBOS)
    monkeypatch.setattr(worker, "_serve", no_serve)
    return StubDBOS


@pytest.mark.req("REL-3", "FR-15.11")
@pytest.mark.wp("P0-07")
@pytest.mark.parametrize("queues", [None, ("extract",)], ids=["worker", "worker-extract"])
def test_app_test_final_1_worker_startup_configures_the_dead_letter_client(
    fresh_process: type[StubDBOS], queues: tuple[str, ...] | None
) -> None:
    """APP-TEST-final finding 1
    `tumnis worker` and `tumnis worker --queues extract` leave the process with the DBOS
    client modules enqueue through (`deadletter.dbos_client`, on the deployment's system
    database), as the api does, besides the database engines, the module kill list and the
    cache backend that publishes invalidations to the other processes.
    """
    settings = Settings(database_url=DSN, database_direct_url=DSN, tumnis_disabled_modules="usage")
    default_cache = cache.backend()

    worker.main(settings, queues=queues)

    assert fresh_process.configs, "the startup built DBOS"
    assert deadletter.dbos_configured()
    assert deadletter._client_url == settings.dbos_system_url
    assert db._state.app_url == settings.database_direct_url
    assert db._state.direct_url == settings.database_direct_url
    assert modules._deployment_disabled == frozenset({"usage"})
    assert cache.backend() is not default_cache
    assert cache.backend()._publish is not None


@pytest.mark.req("FR-15.11", "SEC-5")
@pytest.mark.wp("P3-13")
def test_app_test_final_1_worker_startup_configures_the_s3_source_sync(
    fresh_process: type[StubDBOS],
) -> None:
    """APP-TEST-final finding 1 (the second configure of the same kind)
    The worker hands its SSRF policy to the S3 source sync, as it does to the folder sync,
    instead of leaving it to re-read the environment on every sync.
    """
    settings = Settings(database_url=DSN, database_direct_url=DSN)
    s3_sync = _module("tumnis.modules.knowledge.s3_sync")
    folder_sync = _module("tumnis.modules.knowledge.sync")

    worker.main(settings)

    assert folder_sync._Config.net == settings.net_policy()
    assert s3_sync._Config.net == settings.net_policy()
