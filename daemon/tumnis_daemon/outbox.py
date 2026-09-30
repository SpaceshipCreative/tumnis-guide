"""The daemon's outbox and seen-set (P2-07, FR-5.11): SQLite at `<state_dir>/state.db`.

Every outbound message is written here before a send is attempted and stays until the
server acks it, so neither a dropped socket nor a daemon crash loses anything produced; on
reconnect the rest is replayed in insertion order with the same `message_id`, and the server
stores each id once. The seen-set holds the server commands (`run`, `cancel`) already
taken, by message id and run id, so a resent `run` never starts Hermes twice.

The outbox is bounded (`max_bytes`, 50 MiB by default). When a put takes it past the bound,
the oldest `stream` lines of kind `log` are dropped, and each run's dropped lines are
declared by one `status` message with state `gap` (detail `gap: N lines (seq A-B)`), which
takes the place of the first line it replaces. A further drop merges into that run's
unacked gap under a new message id (the server may already hold the old one; declaring a
range twice is harmless). Results, tool calls, touched files, artifacts and everything else
are never dropped.
"""

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

MAX_BYTES: Final = 50 * 1024 * 1024  # plan default
SEEN_KEEP: Final = timedelta(days=30)  # commands older than this are forgotten

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    kind TEXT,
    run_id TEXT,
    seq INTEGER,
    gap_count INTEGER,
    gap_first INTEGER,
    gap_last INTEGER,
    size INTEGER NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seen (
    message_id TEXT PRIMARY KEY,
    run_id TEXT,
    seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS seen_run ON seen (run_id);
"""


def _encode(message: dict[str, Any]) -> str:
    return json.dumps(message)


def _now() -> datetime:
    return datetime.now(UTC)


def _gap_message(run_id: str, count: int, first: int, last: int) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "message_id": str(uuid4()),
        "correlation_id": f"run:{run_id}",
        "sent_at": _now().isoformat().replace("+00:00", "Z"),
        "type": "status",
        "run_id": run_id,
        "profile": None,
        "profile_version": None,
        "state": "gap",
        "detail": f"gap: {count} lines (seq {first}-{last})",
    }


class Outbox:
    def __init__(self, path: Path, max_bytes: int = MAX_BYTES) -> None:
        self.path = path
        self.max_bytes = max_bytes
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(_SCHEMA)
        path.chmod(0o600)
        cutoff = (_now() - SEEN_KEEP).isoformat()
        self._db.execute("DELETE FROM seen WHERE seen_at < ?", (cutoff,))

    def close(self) -> None:
        self._db.close()

    def __del__(self) -> None:
        # A store dropped without close() (a test, a crash path) closes its connection
        # rather than leaving it to the interpreter.
        db = getattr(self, "_db", None)
        if db is not None:
            db.close()

    # --- the outbox ------------------------------------------------------------------

    def put(self, msg: dict[str, Any]) -> None:
        """Keep the message (durable before the send is attempted); a message already kept
        (same `message_id`) is left as it is. Then make room if the outbox is over its
        bound."""
        body = _encode(msg)
        stream = msg.get("type") == "stream"
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._db.execute(
                "INSERT OR IGNORE INTO outbox (message_id, type, kind, run_id, seq, size, body)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(msg["message_id"]),
                    str(msg.get("type")),
                    msg.get("kind") if stream else None,
                    None if msg.get("run_id") is None else str(msg["run_id"]),
                    msg.get("seq") if stream else None,
                    len(body.encode()),
                    body,
                ),
            )
            self._make_room()

    def _total(self) -> int:
        total: int = self._db.execute("SELECT COALESCE(SUM(size), 0) FROM outbox").fetchone()[0]
        return total

    def _make_room(self) -> None:
        total = self._total()
        while total > self.max_bytes:
            oldest = self._db.execute(
                "SELECT id, run_id, seq, size FROM outbox"
                " WHERE type = 'stream' AND kind = 'log' ORDER BY id LIMIT 1"
            ).fetchone()
            if oldest is None:
                return  # nothing droppable is left
            row_id, run_id, seq, size = oldest
            self._db.execute("DELETE FROM outbox WHERE id = ?", (row_id,))
            total -= size
            gap = self._db.execute(
                "SELECT id, gap_count, gap_first, gap_last, size FROM outbox"
                " WHERE type = 'status' AND gap_count IS NOT NULL AND run_id = ?",
                (run_id,),
            ).fetchone()
            if gap is None:
                gap_id, count, first, last, old_size = row_id, 1, seq, seq, 0
            else:
                gap_id, count, first, last, old_size = gap
                count, first, last = count + 1, min(first, seq), max(last, seq)
                self._db.execute("DELETE FROM outbox WHERE id = ?", (gap_id,))
            body = _encode(_gap_message(run_id, count, first, last))
            new_size = len(body.encode())
            self._db.execute(
                "INSERT INTO outbox (id, message_id, type, run_id, gap_count, gap_first,"
                " gap_last, size, body) VALUES (?, ?, 'status', ?, ?, ?, ?, ?, ?)",
                (
                    gap_id,
                    json.loads(body)["message_id"],
                    run_id,
                    count,
                    first,
                    last,
                    new_size,
                    body,
                ),
            )
            total += new_size - old_size

    def unacked(self) -> list[dict[str, Any]]:
        """Every kept message, in insertion order (a gap stands where its first line was)."""
        rows = self._db.execute("SELECT body FROM outbox ORDER BY id").fetchall()
        return [json.loads(body) for (body,) in rows]

    def ack(self, ids: Iterable[str]) -> list[dict[str, Any]]:
        """Forget the acked messages; returns the ones that were still kept (an ack for an
        unknown id, or a repeated ack, is a no-op)."""
        wanted = [str(i) for i in ids]
        if not wanted:
            return []
        removed: list[dict[str, Any]] = []
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            for message_id in wanted:
                row = self._db.execute(
                    "DELETE FROM outbox WHERE message_id = ? RETURNING body", (message_id,)
                ).fetchone()
                if row is not None:
                    removed.append(json.loads(row[0]))
        return removed

    def depth(self) -> int:
        """How many messages wait for their ack (the heartbeat reports it)."""
        depth: int = self._db.execute("SELECT count(*) FROM outbox").fetchone()[0]
        return depth

    # --- the seen-set ----------------------------------------------------------------

    def seen_command(self, message_id: str) -> bool:
        row = self._db.execute("SELECT 1 FROM seen WHERE message_id = ?", (str(message_id),))
        return row.fetchone() is not None

    def seen_run(self, run_id: str) -> bool:
        row = self._db.execute("SELECT 1 FROM seen WHERE run_id = ?", (str(run_id),))
        return row.fetchone() is not None

    def mark_command(self, message_id: str, run_id: str | None = None) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO seen (message_id, run_id, seen_at) VALUES (?, ?, ?)",
                (str(message_id), None if run_id is None else str(run_id), _now().isoformat()),
            )
