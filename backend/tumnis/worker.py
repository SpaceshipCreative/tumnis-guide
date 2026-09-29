"""DBOS worker: launch, queue registration, relay, schedules (P0-02+)."""


def register_queues() -> None:
    """Register every DBOS queue (A9). Empty until P0-07 adds `events`; the test harness
    calls it before DBOS.launch() exactly as the worker will."""
