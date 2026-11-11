"""Offline buffer on disk (SQLite). Heartbeats wait here until the server acknowledges them.

* Nothing is dropped: if the buffer reaches its size cap, the agent stops COLLECTING (and logs it) rather
  than throwing old data away.
* A batch keeps the same batch_id across retries, so the server can ignore a batch it already stored.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path


class Buffer:
    def __init__(self, path: Path, max_bytes: int = 500 * 1024 * 1024):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.conn = sqlite3.connect(str(self.path), isolation_level=None, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS pending (id INTEGER PRIMARY KEY, payload TEXT NOT NULL,"
            " created_at REAL NOT NULL, batch TEXT)"
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_batch ON pending(batch)")

    def size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                total += p.stat().st_size
        return total

    def full(self) -> bool:
        return self.size_bytes() >= self.max_bytes

    def add(self, heartbeat: dict) -> None:
        self.conn.execute("INSERT INTO pending(payload, created_at) VALUES(?,?)", (json.dumps(heartbeat), time.time()))

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM pending").fetchone()[0]

    def next_batch(self, limit: int = 500) -> tuple[str, list[dict]] | None:
        """Return the open batch (same id on every retry) or form a new one."""
        row = self.conn.execute("SELECT batch FROM pending WHERE batch IS NOT NULL LIMIT 1").fetchone()
        if row is None:
            ids = [r[0] for r in self.conn.execute("SELECT id FROM pending ORDER BY id LIMIT ?", (limit,))]
            if not ids:
                return None
            bid = uuid.uuid4().hex
            self.conn.execute(
                f"UPDATE pending SET batch=? WHERE id IN ({','.join('?' * len(ids))})", (bid, *ids)
            )
        else:
            bid = row[0]
        items = [json.loads(r[0]) for r in self.conn.execute("SELECT payload FROM pending WHERE batch=? ORDER BY id",
                                                             (bid,))]
        return bid, items

    def ack(self, batch_id: str) -> None:
        """Server confirmed it stored the batch -> remove it from the local queue."""
        self.conn.execute("DELETE FROM pending WHERE batch=?", (batch_id,))
