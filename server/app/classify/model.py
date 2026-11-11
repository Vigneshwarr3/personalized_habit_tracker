"""Stage 5: learned window classifier (LightGBM), trained on YOUR labeled 15-minute windows.

* Inactive until there are `min_labeled_windows` (300) labeled windows with activity data.
* Evaluated with time-based cross-validation (train on earlier days, test on later ones; never the future)
  against the routines on exactly the same windows.
* Used only for categories where it beats the routines (F1 by a margin, with enough examples), and it only
  replaces a routine answer that is Unclassified or itself one of those categories. Routines stay the fallback.
* Labels always win; the model never overrides them.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import numpy as np

from .. import categories as cat_svc
from ..config import REPO_ROOT, Config

if TYPE_CHECKING:
    from .pipeline import Classifier

log = logging.getLogger("ht.model")

BASE_FEATURES = ["active_minutes", "idle_ratio", "num_contexts", "largest_context_share", "switch_rate",
                 "media_share", "run_minutes", "signal_confidence", "n_devices", "phone_share",
                 "hour_sin", "hour_cos", "weekday", "is_weekend"]


def model_settings(cfg: Config) -> dict:
    m = cfg.settings.get("model", {}) or {}
    return {
        "enabled": bool(m.get("enabled", True)),
        "min_labeled_windows": int(m.get("min_labeled_windows", 300)),
        "min_class_examples": int(m.get("min_class_examples", 8)),
        "min_support": int(m.get("min_support", 8)),
        "f1_margin": float(m.get("f1_margin", 0.02)),
        "min_probability": float(m.get("min_probability", 0.5)),
        "cv_folds": int(m.get("cv_folds", 4)),
        "train_hour_local": int(m.get("train_hour_local", 4)),
        "dir": m.get("dir", "data/models/window_model"),
    }


def feature_names(signals: list[str]) -> list[str]:
    return BASE_FEATURES + [f"sig_{s}" for s in signals]


def vectorize(window_start: float, f: dict, signals: list[str], tz: ZoneInfo) -> list[float]:
    t = datetime.fromtimestamp(window_start, tz)
    hour = t.hour + t.minute / 60
    src = f.get("source_shares", {}) or {}
    phone = sum(v for k, v in src.items() if k in ("android", "ios"))
    row = [
        f.get("active_minutes", 0), f.get("idle_ratio", 0), f.get("num_contexts", 0), f.get("largest_context_share", 0),
        f.get("switch_rate", 0), f.get("media_share", 0), f.get("run_minutes", 0), f.get("signal_confidence", 0),
        len(f.get("devices", [])), phone, math.sin(2 * math.pi * hour / 24), math.cos(2 * math.pi * hour / 24),
        t.weekday(), int(t.weekday() >= 5),
    ]
    sig = f.get("signals", {}) or {}
    return [float(x) for x in row] + [float(sig.get(s, 0.0)) for s in signals]


# ------------------------------------------------------------------ dataset
def label_signature(conn) -> str:
    r = conn.execute("SELECT COUNT(*), COALESCE(MAX(id),0), COALESCE(MAX(deactivated_at),0) FROM labels").fetchone()
    return f"{r[0]}:{r[1]}:{r[2]}"


def build_dataset(conn, classifier: "Classifier") -> list[dict]:
    """One row per labeled window that has activity data: features, your label, and the routines' answer."""
    cfg = classifier.cfg
    w = cfg.window_s
    span = conn.execute("SELECT MIN(start_ts), MAX(end_ts) FROM labels WHERE active=1").fetchone()
    if span[0] is None:
        return []
    labels = [dict(r) for r in conn.execute(
        "SELECT start_ts, end_ts, category_id FROM labels WHERE active=1 ORDER BY created_at DESC, id DESC")]
    min_overlap = float(cfg.cls.get("label_min_overlap", 0.5)) * w
    tz = ZoneInfo(cfg.timezone)
    rows = []
    windows = classifier.classify_range(conn, span[0], span[1], include_hidden=True, use_labels=False, use_model=False)
    for wd in windows:
        f = wd["features"]
        if not f:
            continue
        a, b = wd["window_start"], wd["window_end"]
        lb = next((l for l in labels if max(0.0, min(b, l["end_ts"]) - max(a, l["start_ts"])) >= min_overlap), None)
        if lb is None:
            continue
        rows.append({"ts": a, "x": vectorize(a, f, cfg.signals, tz), "y": cat_svc.resolve_id(conn, lb["category_id"]),
                     "baseline": wd["category_id"]})
    rows.sort(key=lambda r: r["ts"])
    return rows


def time_folds(n: int, k: int) -> list[tuple[list[int], list[int]]]:
    """Expanding-window splits over time-sorted rows: train on chunks[0..i], test on chunk i+1."""
    if n < k + 1:
        return []
    edges = [round(n * i / (k + 1)) for i in range(k + 2)]
    return [(list(range(0, edges[i])), list(range(edges[i], edges[i + 1]))) for i in range(1, k + 1)]


def per_class_metrics(y_true: list, y_pred: list, classes: list) -> dict:
    out = {}
    for c in classes:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == c and p == c)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != c and p == c)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == c and p != c)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        out[c] = {"precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3), "support": tp + fn}
    return out


def _fit(X: np.ndarray, y: np.ndarray, n_classes: int):
    import lightgbm as lgb
    params = {"objective": "multiclass", "num_class": n_classes, "learning_rate": 0.08, "num_leaves": 15,
              "min_data_in_leaf": 5, "feature_fraction": 0.9, "bagging_fraction": 0.9, "bagging_freq": 1,
              "lambda_l2": 1.0, "verbose": -1, "seed": 42, "deterministic": True, "num_threads": 2}
    return lgb.train(params, lgb.Dataset(X, label=y), num_boost_round=150)


def combine(baseline, model_cls, model_p, enabled: set, min_p: float):
    """The serving rule: the model answers only for categories where it beat the routines."""
    if model_cls in enabled and model_p >= min_p and (baseline is None or baseline in enabled):
        return model_cls, "model"
    return baseline, "routine"


# ------------------------------------------------------------------ training + serving
class WindowModel:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.s = model_settings(cfg)
        d = Path(self.s["dir"])
        self.dir = d if d.is_absolute() else REPO_ROOT / d
        self.tz = ZoneInfo(cfg.timezone)
        self._lock = threading.Lock()
        self._booster = None
        self._meta: dict = {}
        self._mtime = 0.0
        self.reload()

    @property
    def meta(self) -> dict:
        return self._meta

    def reload(self) -> None:
        mp = self.dir / "meta.json"
        try:
            mt = mp.stat().st_mtime
        except OSError:
            self._booster, self._meta, self._mtime = None, {}, 0.0
            return
        if mt == self._mtime:
            return
        meta = json.loads(mp.read_text())
        booster = None
        if meta.get("active") and (self.dir / "model.txt").exists():
            import lightgbm as lgb
            booster = lgb.Booster(model_file=str(self.dir / "model.txt"))
        with self._lock:
            self._booster, self._meta, self._mtime = booster, meta, mt

    @property
    def active(self) -> bool:
        return self.s["enabled"] and self._booster is not None and bool(self._meta.get("enabled_categories"))

    def predict(self, window_start: float, f: dict) -> tuple[int, float] | None:
        if not self.active:
            return None
        names = self._meta["feature_names"]
        sigs = [n[4:] for n in names if n.startswith("sig_")]
        x = np.array([vectorize(window_start, f, sigs, self.tz)])
        proba = self._booster.predict(x)[0]
        i = int(np.argmax(proba))
        return int(self._meta["classes"][i]), float(proba[i])

    def serve(self, window_start: float, f: dict, baseline: int | None) -> tuple[int, float] | None:
        pred = self.predict(window_start, f)
        if pred is None:
            return None
        cls, p = pred
        enabled = set(self._meta["enabled_categories"])
        chosen, src = combine(baseline, cls, p, enabled, self.s["min_probability"])
        return (chosen, p) if src == "model" else None

    def train(self, conn, classifier: "Classifier") -> dict:
        s = self.s
        rows = build_dataset(conn, classifier)
        n = len(rows)
        meta: dict = {"trained_at": time.time(), "n_labeled": n, "min_labeled_windows": s["min_labeled_windows"],
                      "label_signature": label_signature(conn), "active": False, "enabled_categories": [],
                      "metrics": {}, "classes": [], "note": ""}
        if n < s["min_labeled_windows"]:
            meta["note"] = f"needs {s['min_labeled_windows']} labeled windows with activity; has {n}"
            return self._save(meta, None)

        counts = Counter(r["y"] for r in rows)
        classes = sorted(c for c, k in counts.items() if k >= s["min_class_examples"])
        idx = {c: i for i, c in enumerate(classes)}
        rows_m = [r for r in rows if r["y"] in idx]
        X = np.array([r["x"] for r in rows_m], dtype=float)
        y = np.array([idx[r["y"]] for r in rows_m])

        # time-based CV: model vs routines on the same held-out (later) windows
        y_true, y_model, p_model, y_base = [], [], [], []
        for tr, te in time_folds(len(rows_m), s["cv_folds"]):
            if len(set(y[tr].tolist())) < 2:
                continue
            booster = _fit(X[tr], y[tr], len(classes))
            proba = booster.predict(X[te])
            for j, row_i in enumerate(te):
                k = int(np.argmax(proba[j]))
                y_true.append(rows_m[row_i]["y"])
                y_model.append(classes[k])
                p_model.append(float(proba[j][k]))
                y_base.append(rows_m[row_i]["baseline"])
        m_model = per_class_metrics(y_true, y_model, classes)
        m_base = per_class_metrics(y_true, y_base, classes)
        enabled = [c for c in classes
                   if m_model[c]["support"] >= s["min_support"]
                   and m_model[c]["f1"] >= m_base[c]["f1"] + s["f1_margin"]]
        combined = [combine(b, m, p, set(enabled), s["min_probability"])[0] for b, m, p in zip(y_base, y_model, p_model)]
        acc = lambda pred: round(sum(1 for t, q in zip(y_true, pred) if t == q) / len(y_true), 3) if y_true else None
        meta.update({
            "classes": classes, "feature_names": feature_names(classifier.cfg.signals),
            "enabled_categories": enabled, "active": bool(enabled), "cv_windows": len(y_true),
            "accuracy": {"routines": acc(y_base), "model": acc(y_model), "combined": acc(combined)},
            "metrics": {str(c): {"model": m_model[c], "routines": m_base[c], "enabled": c in enabled} for c in classes},
            "note": "" if enabled else "the model did not beat the routines for any category yet",
        })
        final = _fit(X, y, len(classes)) if enabled else None
        return self._save(meta, final)

    def _save(self, meta: dict, booster) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        if booster is not None:
            tmp = self.dir / "model.txt.tmp"
            booster.save_model(str(tmp))
            os.replace(tmp, self.dir / "model.txt")
        tmp = self.dir / "meta.json.tmp"
        tmp.write_text(json.dumps(meta, indent=1))
        os.replace(tmp, self.dir / "meta.json")  # older model files are simply replaced; your labels are the data
        self._mtime = 0.0
        self.reload()
        return meta
