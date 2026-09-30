"""The Tumnis skill harness (P1-05): recorded packets through the real Hermes, asserted on
tool calls and JSON, three runs per case.

The harness runs a case the way production does: the daemon's own `hermes_argv` and
stream-json reader (../daemon), and the backend's pure skill models and cross-field rules
(../backend/tumnis/modules/agents). Both folders go on sys.path here, before any harness
module imports them; only modules that need nothing beyond pydantic are imported.
"""

import sys
from pathlib import Path
from typing import Final

REPO: Final = Path(__file__).resolve().parents[2]
for _source in (REPO / "backend", REPO / "daemon"):
    if str(_source) not in sys.path:
        sys.path.append(str(_source))
