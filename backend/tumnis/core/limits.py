"""Shared caps (A1, R-11). Pure constants: rules may import them."""

from datetime import timedelta
from typing import Final

# The longest estimate a task may carry, in minutes of human time (plan default: one
# 16-hour stretch). P0-18 validates task estimates with it; P1-08's enrichment reuses it.
MAX_ESTIMATE_MINUTES: Final = 960

# Runs (P2-04, R-29, R-30): the one re-arm interval for human waits, and the wall-clock
# ceiling of any run, waiting included; both plan defaults (the ceiling is flagged for
# Scott in Part B). Tests shorten them through `agents.api.configure_runs` (fakes only).
WAIT_SLICE_S: Final = 3600
RUN_WALL_CLOCK_CEILING: Final = timedelta(hours=24)
