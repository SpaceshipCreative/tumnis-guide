"""Moving a project's folder to another location (P3-14, FR-15.12): copy, verify by hash,
switch, keep the old copy.

Red-phase seam: the spec tests turn it green.
"""

from collections.abc import Callable

Fault = Callable[[str, bytes], bytes]


def use_fault(fn: Fault | None) -> Fault | None:
    """Test-only: pass every copied file's bytes through `fn(path, data)` (fault
    injection); returns the previous hook. None removes it."""
    raise NotImplementedError("P3-14")
