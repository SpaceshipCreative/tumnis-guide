"""decisions pure rules (P1-01): the label reason line, option keys for project matching
and model pinning. No I/O; nothing here reads the clock.

The question catalogue and the outbound payload builder live in `catalog.py`: they need
`unicodedata` and `json`, which the rules allow-list (T-P0-01-09) does not include.
"""

import re
from collections.abc import Mapping
from typing import Final, Literal

# --- Label reason (FR-4.1): Jev returns no reason text ----------------------------------

HUMAN_SIGNALS: Final[dict[str, str]] = {  # companion Noul id -> reason phrase, in tie order
    "needs_signature": "Needs a signature",
    "needs_decision": "Needs your decision",
    "needs_relationship": "Depends on a conversation",
    "needs_presence": "Needs someone there in person",
    "reserved_judgment": "A judgment you reserved",
}
SIGNAL_FLOOR: Final = 0.5  # plan default: a companion counts from a yes probability of 0.5
HUMAN_FALLBACK: Final = "Needs your judgment"
AI_VERIFIABLE: Final = "Fully specified and verifiable"
AI_FALLBACK: Final = "Looks automatable"
HYBRID_PREFIX: Final = "Part automatable, part needs you: "
MAX_REASON_CHARS: Final = 80


def _human_reason(companions: Mapping[str, float]) -> str:
    best, best_value = HUMAN_FALLBACK, SIGNAL_FLOOR
    for signal, phrase in HUMAN_SIGNALS.items():
        value = companions.get(signal, 0.0)
        if value > best_value or (value == best_value and best == HUMAN_FALLBACK):
            best, best_value = phrase, value
    return best


def label_reason(label: Literal["human", "ai", "hybrid"], companions: Mapping[str, float]) -> str:
    """Pick the strongest companion signal that agrees with the label; <= 80 chars, one line.

    human: the highest of the five human companions at 0.5 or above, else `Needs your
    judgment`. ai: `Fully specified and verifiable` when `specified_verifiable` is at 0.5
    or above, else `Looks automatable`. hybrid: `Part automatable, part needs you: ` and
    the human reason, lower-cased. A missing companion counts as 0.
    """
    if label == "ai":
        verifiable = companions.get("specified_verifiable", 0.0) >= SIGNAL_FLOOR
        return AI_VERIFIABLE if verifiable else AI_FALLBACK
    human = _human_reason(companions)
    reason = human if label == "human" else HYBRID_PREFIX + human[0].lower() + human[1:]
    return reason[:MAX_REASON_CHARS]


# --- Project match option keys ---------------------------------------------------------
#
# Option keys are sent to the model, so they carry no data: `p01`..`p254` by position in
# the project list the caller passed, mapped back here. Project UUIDs never leave.

_OPTION_KEY: Final = re.compile(r"^p(\d{2,3})$")


def option_key(position: int) -> str:
    """1 -> "p01", 99 -> "p99", 100 -> "p100"."""
    if position < 1:
        raise ValueError("option positions start at 1")
    return f"p{position:02d}"


def option_position(key: str) -> int | None:
    """The 1-based position an option key names; None for `unknown` or anything else."""
    match = _OPTION_KEY.match(key)
    position = 0 if match is None else int(match.group(1))
    if position < 1 or option_key(position) != key:
        return None
    return position


# --- Model pinning (FR-11.2): always a versioned id, never an alias ---------------------

_PINNED: Final = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*-\d+\.\d+\.\d+$")


def is_pinned_model(model: str) -> bool:
    """`jev-1.13.0` is pinned; `jev-latest`, `jev` and `jev-1.13` are aliases that move."""
    return bool(_PINNED.match(model))


# --- Generation slot output (P1-03, FR-11.8): one short line or nothing -----------------

_LIST_MARKER: Final = re.compile(r"^(?:[-*\u2022>#]+|\d+[.)])\s+")
# Straight, curly and back quotes and markdown emphasis around the whole line.
_WRAPPERS: Final = "\"'`*_\u201c\u201d\u2018\u2019"
_SENTENCE_END: Final = re.compile(r"(?<=[.!?])\s+")
_TRAILING_CUT: Final = " ,;:-\u2013\u2014"  # dangling punctuation after a cut, dashes too


def one_line(text: str, max_chars: int) -> str | None:
    """The first line of `text` that holds a letter or digit, without a list marker or
    wrapping quotes, cut to its first sentence and to `max_chars` at a word boundary.
    None when nothing is left: empty output, or output that is only punctuation."""
    line = next((ln for ln in text.splitlines() if any(ch.isalnum() for ch in ln)), None)
    if line is None:
        return None
    line = _LIST_MARKER.sub("", line.strip()).strip(_WRAPPERS).strip()
    line = _SENTENCE_END.split(line, maxsplit=1)[0]
    if len(line) > max_chars:
        cut = line[:max_chars]
        line = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(_TRAILING_CUT)
    return line if any(ch.isalnum() for ch in line) else None
