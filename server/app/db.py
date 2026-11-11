"""SQLite storage (WAL mode). Collected data is append-only: nothing in this codebase deletes heartbeats,
labels, categories or hidden-range records. 'Undo'-style operations set flags/timestamps instead."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .config import Config

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- Raw activity. Consecutive identical heartbeats from one device are stored as one span (start_ts..end_ts).
CREATE TABLE IF NOT EXISTS heartbeats (
    id          INTEGER PRIMARY KEY,
    device      TEXT NOT NULL,
    source      TEXT NOT NULL,
    app         TEXT NOT NULL,
    title       TEXT,
    url         TEXT,             -- sanitized: no query string/fragment (youtube ?v= kept)
    domain      TEXT,
    idle        INTEGER NOT NULL DEFAULT 0,
    media       INTEGER NOT NULL DEFAULT 0,  -- media playing while there was no keyboard/mouse input
    start_ts    REAL NOT NULL,    -- unix seconds, UTC
    end_ts      REAL NOT NULL,
    n_samples   INTEGER NOT NULL DEFAULT 1,
    received_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hb_start ON heartbeats(start_ts);
CREATE INDEX IF NOT EXISTS idx_hb_end ON heartbeats(end_ts);
CREATE INDEX IF NOT EXISTS idx_hb_device_end ON heartbeats(device, end_ts);

CREATE TABLE IF NOT EXISTS ingested_batches (
    batch_id    TEXT PRIMARY KEY,
    device      TEXT NOT NULL,
    n           INTEGER NOT NULL,
    received_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    color       TEXT NOT NULL,
    archived    INTEGER NOT NULL DEFAULT 0,
    merged_into INTEGER REFERENCES categories(id),
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
-- Old names keep resolving after a rename, so rules/routines/YAML never break.
CREATE TABLE IF NOT EXISTS category_aliases (
    alias       TEXT PRIMARY KEY COLLATE NOCASE,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS category_events (
    id          INTEGER PRIMARY KEY,
    ts          REAL NOT NULL,
    category_id INTEGER NOT NULL,
    action      TEXT NOT NULL,
    detail      TEXT
);

-- Corrections from the dashboard. Newest active label wins. 'Undo' sets active=0 (history kept).
CREATE TABLE IF NOT EXISTS labels (
    id          INTEGER PRIMARY KEY,
    start_ts    REAL NOT NULL,
    end_ts      REAL NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    note        TEXT,
    source      TEXT NOT NULL DEFAULT 'correction',  -- correction (you changed it) | confirmed (you OK'd it)
    created_at  REAL NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    deactivated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_labels_range ON labels(start_ts, end_ts);

-- Ranges hidden from the dashboard. Data is kept; un-hiding sets unhidden_at.
CREATE TABLE IF NOT EXISTS hidden_ranges (
    id          INTEGER PRIMARY KEY,
    start_ts    REAL NOT NULL,
    end_ts      REAL NOT NULL,
    reason      TEXT,
    created_at  REAL NOT NULL,
    unhidden_at REAL
);

CREATE TABLE IF NOT EXISTS devices (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    source      TEXT NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    created_at  REAL NOT NULL,
    revoked_at  REAL,
    last_seen   REAL
);

CREATE TABLE IF NOT EXISTS settings_kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pause_events (
    id     INTEGER PRIMARY KEY,
    ts     REAL NOT NULL,
    paused INTEGER NOT NULL,
    note   TEXT
);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI may open a request's connection (dependency) on one worker thread and run
    # the endpoint on another; parallel requests then failed. Each connection is still used by one request at a time.
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None,  # autocommit; explicit BEGIN when needed
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db(cfg: Config) -> None:
    conn = connect(cfg.db_path)
    try:
        conn.executescript(SCHEMA)
        # migrations for databases created by older versions (columns are only ever added)
        if "source" not in {r[1] for r in conn.execute("PRAGMA table_info(labels)")}:
            conn.execute("ALTER TABLE labels ADD COLUMN source TEXT NOT NULL DEFAULT 'correction'")
        from .kg.graph import KnowledgeGraph  # knowledge-graph tables (phase 2)
        KnowledgeGraph.init_schema(conn)
        from .location import LOCATION_SCHEMA, add_columns as _loc_cols  # phone location (coarse) + places
        conn.executescript(LOCATION_SCHEMA)
        _loc_cols(conn)
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None:
            conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
        seeded = conn.execute("SELECT value FROM meta WHERE key='categories_seeded'").fetchone()
        if seeded is None:
            now = time.time()
            conn.execute("BEGIN")
            for i, c in enumerate(cfg.categories_seed):
                conn.execute(
                    "INSERT OR IGNORE INTO categories(id, name, color, sort_order, created_at, updated_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (int(c["id"]), str(c["name"]), str(c["color"]), i, now, now),
                )
            conn.execute("INSERT INTO meta(key, value) VALUES('categories_seeded', '1')")
            conn.execute("COMMIT")
        from . import taxonomy as _tx
        _tx.add_columns(conn)  # columns must exist before any category is read
        if conn.execute("SELECT value FROM meta WHERE key='categories_v2'").fetchone() is None:
            # added later at your request; skipped if you already have a category with that name
            from . import categories as cat_svc
            for name, color in (("Office/Work", "#3E444C"), ("Commute", "#3FA7A3")):
                if cat_svc.find_by_name(conn, name) is None:
                    cat_svc.add(conn, name, color)
            conn.execute("INSERT INTO meta(key, value) VALUES('categories_v2', '1')")
        from . import taxonomy  # Activities > Sub-activities
        taxonomy.add_columns(conn)
        taxonomy.migrate(conn)
    finally:
        conn.close()
