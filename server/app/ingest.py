"""Heartbeat ingestion: sanitize, de-duplicate batches, and merge consecutive identical heartbeats into spans.

Merging is lossless for what we use: the same (app, title, url, idle, media) from the same device,
back to back, becomes one row whose start_ts..end_ts covers all of them (n_samples counts them).
"""
from __future__ import annotations

import sqlite3
import time
from typing import Iterable

from .config import Config
from .privacy import Privacy
from .sanitize import sanitize_url

MAX_TITLE = 1000
MAX_APP = 200


def _clean(s: str | None, limit: int) -> str | None:
    if s is None:
        return None
    s = " ".join(str(s).split())  # collapse whitespace/newlines
    return s[:limit] or None


def ingest(
    conn: sqlite3.Connection,
    cfg: Config,
    heartbeats: Iterable[dict],
    device: str,
    batch_id: str | None = None,
    now: float | None = None,
) -> dict:
    now = time.time() if now is None else now
    gap = float(cfg.ingest.get("merge_gap_seconds", 20))
    skew = float(cfg.ingest.get("max_future_skew_seconds", 300))

    if batch_id:
        seen = conn.execute("SELECT n FROM ingested_batches WHERE batch_id=?", (batch_id,)).fetchone()
        if seen is not None:
            return {"accepted": 0, "merged": 0, "rejected": 0, "duplicate_batch": True, "new_rows": []}

    privacy = Privacy.from_settings(cfg.settings)
    items = sorted(heartbeats, key=lambda h: float(h["ts"]))
    accepted = merged = rejected = 0
    new_rows: list[dict] = []  # spans created by this batch (the knowledge graph looks at each once)

    conn.execute("BEGIN IMMEDIATE")
    try:
        last = conn.execute(
            "SELECT * FROM heartbeats WHERE device=? ORDER BY end_ts DESC, id DESC LIMIT 1", (device,)
        ).fetchone()
        last = dict(last) if last else None

        for hb in items:
            ts = float(hb["ts"])
            interval = float(hb.get("interval_s") or 0)
            if ts > now + skew or interval <= 0:
                rejected += 1
                continue
            url, domain = sanitize_url(hb.get("url"))
            row = {
                "device": device,
                "source": str(hb.get("source") or "unknown")[:20],
                "app": _clean(hb.get("app"), MAX_APP) or "unknown",
                "title": _clean(hb.get("title"), MAX_TITLE),
                "url": url,
                "domain": domain,
                "idle": int(bool(hb.get("idle"))),
                "media": int(bool(hb.get("media"))),
                "start_ts": ts,
                "end_ts": ts + interval,
            }
            row = privacy.redact(row)  # sensitive apps/sites/titles: keep only app name + bare site
            key = (row["app"], row["title"], row["url"], row["idle"], row["media"])
            if (
                last is not None
                and (last["app"], last["title"], last["url"], last["idle"], last["media"]) == key
                and last["start_ts"] - 1e-6 <= ts <= last["end_ts"] + gap
            ):
                new_end = max(last["end_ts"], row["end_ts"])
                conn.execute(
                    "UPDATE heartbeats SET end_ts=?, n_samples=n_samples+1 WHERE id=?", (new_end, last["id"])
                )
                last["end_ts"] = new_end
                merged += 1
            else:
                cur = conn.execute(
                    "INSERT INTO heartbeats(device, source, app, title, url, domain, idle, media, start_ts, end_ts,"
                    " n_samples, received_at) VALUES(?,?,?,?,?,?,?,?,?,?,1,?)",
                    (row["device"], row["source"], row["app"], row["title"], row["url"], row["domain"],
                     row["idle"], row["media"], row["start_ts"], row["end_ts"], now),
                )
                new_rows.append(row)
                # only extend forward in time; an out-of-order old heartbeat doesn't become "last"
                if last is None or row["end_ts"] >= last["end_ts"]:
                    last = row | {"id": cur.lastrowid}
            accepted += 1

        if batch_id:
            conn.execute(
                "INSERT INTO ingested_batches(batch_id, device, n, received_at) VALUES(?,?,?,?)",
                (batch_id, device, accepted, now),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"accepted": accepted, "merged": merged, "rejected": rejected, "duplicate_batch": False,
            "new_rows": new_rows}
