"""Loads YAML config. Everything is overridable with env vars so tests and Docker can point elsewhere.

HT_CONFIG_DIR  directory holding settings.yaml / categories.yaml / rules.yaml / routines.yaml
HT_DB_PATH     sqlite file path (overrides settings.yaml database.path)
HT_ADMIN_TOKEN token the dashboard (and you) use for admin endpoints
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = REPO_ROOT / "server" / "config"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass
class Config:
    config_dir: Path
    settings: dict[str, Any]
    categories_seed: list[dict[str, Any]]
    rules: list[dict[str, Any]]
    routines: list[dict[str, Any]]
    db_path: Path
    admin_token: str | None = None
    owner_login: str | None = None   # Tailscale login allowed to use the web app (env HT_OWNER_LOGIN)
    extra: dict[str, Any] = field(default_factory=dict)

    # convenience accessors -------------------------------------------------
    @property
    def timezone(self) -> str:
        return self.settings.get("timezone", "UTC")

    @property
    def cls(self) -> dict[str, Any]:
        return self.settings.get("classification", {})

    @property
    def window_s(self) -> int:
        return int(self.cls.get("window_minutes", 15)) * 60

    @property
    def signals(self) -> list[str]:
        return list(self.settings.get("signals", []))

    @property
    def browsers(self) -> set[str]:
        return {b.lower() for b in self.settings.get("browsers", [])}

    @property
    def ingest(self) -> dict[str, Any]:
        return self.settings.get("ingest", {})


def load_config(config_dir: str | os.PathLike | None = None, db_path: str | os.PathLike | None = None) -> Config:
    cdir = Path(config_dir or os.environ.get("HT_CONFIG_DIR") or DEFAULT_CONFIG_DIR)
    settings = _load_yaml(cdir / "settings.yaml")
    cats = _load_yaml(cdir / "categories.yaml").get("categories", [])
    rules = _load_yaml(cdir / "rules.yaml").get("rules", [])
    routines = _load_yaml(cdir / "routines.yaml").get("routines", [])
    dbp = db_path or os.environ.get("HT_DB_PATH") or settings.get("database", {}).get("path", "data/tracker.db")
    dbp = Path(dbp)
    if not dbp.is_absolute():
        dbp = REPO_ROOT / dbp
    return Config(
        config_dir=cdir,
        settings=settings,
        categories_seed=cats,
        rules=rules,
        routines=routines,
        db_path=dbp,
        admin_token=os.environ.get("HT_ADMIN_TOKEN") or None,
        owner_login=(os.environ.get("HT_OWNER_LOGIN") or "").strip().lower() or None,
    )


@lru_cache(maxsize=1)
def get_config() -> Config:
    return load_config()
