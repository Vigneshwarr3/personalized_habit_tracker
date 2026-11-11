"""Nightly, consistent SQLite backups (gzip). Backups are never deleted or overwritten."""
from __future__ import annotations

import gzip
import logging
import os
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import REPO_ROOT, Config

log = logging.getLogger("ht.backup")


def backup_dir(cfg: Config) -> Path:
    d = Path(cfg.settings.get("backup", {}).get("dir", "data/backups"))
    return d if d.is_absolute() else REPO_ROOT / d


def backup_now(cfg: Config, label: str | None = None) -> Path | None:
    """Write data/backups/tracker-<label>.db.gz. Returns None if that backup already exists."""
    label = label or datetime.now(ZoneInfo(cfg.timezone)).strftime("%Y-%m-%d")
    out_dir = backup_dir(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / f"tracker-{label}.db.gz"
    if final.exists():
        return None
    tmp_db = out_dir / f".tmp-{label}.db"
    src = sqlite3.connect(str(cfg.db_path))
    dst = sqlite3.connect(str(tmp_db))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    tmp_gz = out_dir / f".tmp-{label}.db.gz"
    with tmp_db.open("rb") as fin, gzip.open(tmp_gz, "wb", compresslevel=6) as fout:
        shutil.copyfileobj(fin, fout)
    os.replace(tmp_gz, final)
    tmp_db.unlink(missing_ok=True)  # temp file only, the backup itself is kept
    log.info("backup written: %s", final)
    return final


def start_scheduler(cfg: Config, stop: threading.Event) -> threading.Thread:
    hour = int(cfg.settings.get("backup", {}).get("hour_local", 3))

    def loop():
        while not stop.is_set():
            try:
                now = datetime.now(ZoneInfo(cfg.timezone))
                if now.hour >= hour:
                    backup_now(cfg)
            except Exception:  # never let backup problems take the API down
                log.exception("backup failed")
            stop.wait(600)

    t = threading.Thread(target=loop, name="ht-backup", daemon=True)
    t.start()
    return t
