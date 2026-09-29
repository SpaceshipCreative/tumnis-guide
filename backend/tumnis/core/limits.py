"""Shared caps (A1, R-11). Pure constants: rules may import them."""

from typing import Final

# The longest estimate a task may carry, in minutes of human time (plan default: one
# 16-hour stretch). P0-18 validates task estimates with it; P1-08's enrichment reuses it.
MAX_ESTIMATE_MINUTES: Final = 960
