"""The `dbos` fixture closes every system-database connection it opened (issue #35).

DBOS (3.1.0) drops a workflow from its active set before it writes the workflow's outcome,
so the fixture's teardown could find nothing active, destroy DBOS and dispose its engine
while an executor thread was still writing: that connection went back to the disposed pool
open, and the garbage collector reported it ("psycopg.Connection ... deleted while still
open") in whatever test ran next. Here the outcome write is held until DBOS is destroyed.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

import pytest
from dbos import DBOS

if TYPE_CHECKING:
    from tests._pg import DbUrls

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@DBOS.workflow()
def issue_35_workflow() -> str:
    return "done"


def _when_destroyed(then: threading.Event) -> None:
    """Set `then` once the fixture's teardown has destroyed the DBOS instance."""
    from dbos import _dbos  # noqa: PLC0415  # dbos 3.1.0: the global instance

    deadline = time.monotonic() + 30
    while _dbos._dbos_global_instance is not None and time.monotonic() < deadline:
        time.sleep(0.005)
    then.set()


@pytest.mark.req("Quality rule 5")
@pytest.mark.wp("P0-02")
def test_issue_35_teardown_closes_a_connection_still_writing_an_outcome(
    db: DbUrls, dbos_sys_db: DbUrls
) -> None:
    from dbos._dbos import _get_dbos_instance  # noqa: PLC0415

    from tests.fixtures import dbos as dbos_fixture  # noqa: PLC0415

    # The fixture itself, driven by hand so its teardown runs inside the test.
    lifecycle = dbos_fixture._get_wrapped_function()(db, dbos_sys_db)
    next(lifecycle)
    sys_db = _get_dbos_instance()._sys_db
    engine, first_pool = sys_db.engine, sys_db.engine.pool
    writing, release, written = threading.Event(), threading.Event(), threading.Event()
    held: dict[str, Any] = {}
    write_outcome = sys_db.update_workflow_outcome

    def slow_outcome_write(*args: Any, **kwargs: Any) -> Any:
        # A pooled connection held the way a slow outcome write holds one.
        with engine.connect() as conn:
            held["dbapi"] = conn.connection.dbapi_connection
            writing.set()
            release.wait(30)
            try:
                return write_outcome(*args, **kwargs)
            finally:
                written.set()

    sys_db.update_workflow_outcome = slow_outcome_write  # type: ignore[method-assign]
    try:
        DBOS.start_workflow(issue_35_workflow)
        assert writing.wait(10), "the workflow never reached its outcome write"
        threading.Thread(target=_when_destroyed, args=(release,), daemon=True).start()

        next(lifecycle, None)  # the fixture's teardown

        assert written.wait(10), "the outcome write never finished"
        assert held["dbapi"].closed, "teardown left a system-database connection open"
    finally:
        release.set()
        written.wait(10)
        first_pool.dispose()  # without the fix, nothing else closes them
        engine.dispose()
