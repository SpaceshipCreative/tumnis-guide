"""Imported by the queue-isolation test's worker subprocesses (`run_worker --import`): a
workflow that does nothing, so the test can enqueue it on any queue and see which worker
ran it (`WorkflowStatus.executor_id`, T-P1-16-12)."""

from dbos import DBOS

PROBE = "knowledge_queue_probe"


@DBOS.workflow(name=PROBE)
async def queue_probe(label: str) -> str:
    return label
