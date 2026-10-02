"""Imported by the purge kill test's worker subprocesses (`run_worker --import`): purge
batches of two records with no pause between them, so a handful of messages makes several
batches (the product's batch is 500, T-P3-09-07)."""

from tumnis.modules.integrations import workflows

# setattr: the names arrive with the workflows themselves (P3-09).
setattr(workflows, "PURGE_BATCH", 2)  # noqa: B010
setattr(workflows, "PURGE_PAUSE_S", 0.0)  # noqa: B010
