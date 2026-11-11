"""Classification pipeline, per 15-minute window:

  1. user labels (corrections)            -> category, always win
  2. rules.yaml                           -> signal per activity item
  3. knowledge graph + LLM (phase 2)      -> signal for unknown / low-confidence items
  4. routines.yaml                        -> category from the window's signal mix + features
  5. learned model (phase 4)              -> category, only where it beats the routines

Windows with no label and less than `min_active_seconds` of activity are left blank.
"""
from __future__ import annotations

import math
import sqlite3
from typing import TYPE_CHECKING

from .. import categories as cat_svc
from ..config import Config
from .features import bucket_segments, fill_run_minutes, window_features
from .routines import first_match, load_routines
from .rules import RuleEngine, SignalHit

if TYPE_CHECKING:
    from ..kg.graph import KnowledgeGraph

SEGMENT_COLS = "id, device, source, app, title, url, domain, idle, media, start_ts, end_ts"


class Classifier:
    def __init__(self, cfg: Config, kg: "KnowledgeGraph | None" = None, model=None, locator=None):
        self.cfg = cfg
        self.model = model  # stage 5: learned model (WindowModel), only where it beats the routines
        self.locator = locator  # stage 6: phone location fills blocks that are still empty / unclassified
        self.ui_fn = None       # conn -> dashboard settings (session thresholds); set by the app
        self.rules = RuleEngine(cfg.rules, cfg.signals)
        self.routines = load_routines(cfg.routines, cfg.signals)
        self.kg = kg  # stage 3: knowledge graph for unknown / low-confidence items

    # ---- stage 2: activity item -> signal (rules only) --------------------------------------
    def rule_signal(self, seg: dict) -> SignalHit:
        return self.rules.match(seg["app"], seg.get("domain"), seg.get("url"), seg.get("title"))

    def signal_fn(self, conn: sqlite3.Connection):
        """Stages 2+3 for one read: rules, then the knowledge graph (your KG corrections win over rules)."""
        resolve = self.kg.resolver(conn) if self.kg is not None else None
        cache: dict[tuple, SignalHit] = {}

        def signal_of(seg: dict) -> SignalHit:
            k = (seg["app"], seg.get("domain"), seg.get("url"), seg.get("title"))
            if k not in cache:
                hit = self.rule_signal(seg)
                hit = resolve(seg, hit) if resolve else hit
                dom = (seg.get("domain") or "")
                if hit.signal == "education" and (dom in ("youtube.com", "youtu.be") or seg["app"] == "com.google.android.youtube"):
                    hit = SignalHit("video_learning", hit.confidence, hit.source, hit.rule_index)  # a YouTube lesson
                cache[k] = hit
            return cache[k]

        return signal_of

    # ---- whole range --------------------------------------------------------------------------
    def classify_range(
        self, conn: sqlite3.Connection, start_ts: float, end_ts: float, include_hidden: bool = False,
        use_labels: bool = True, use_model: bool = True,
    ) -> list[dict]:
        w = self.cfg.window_s
        start = math.floor(start_ts / w) * w
        end = math.ceil(end_ts / w) * w
        min_active = float(self.cfg.cls.get("min_active_seconds", 60))
        min_overlap = float(self.cfg.cls.get("label_min_overlap", 0.5)) * w

        segs = [
            dict(r)
            for r in conn.execute(
                f"SELECT {SEGMENT_COLS} FROM heartbeats WHERE end_ts > ? AND start_ts < ? ORDER BY start_ts",
                (start, end),
            )
        ]
        buckets = bucket_segments(segs, start, end, w)
        signal_of = self.signal_fn(conn)
        feats = {
            i: window_features(
                items, w, self.cfg.browsers, float(self.cfg.cls.get("context_min_share", 0.10)), signal_of
            )
            for i, items in buckets.items()
        }
        fill_run_minutes(feats, min_active)

        labels = [] if not use_labels else [
            dict(r)
            for r in conn.execute(
                "SELECT id, start_ts, end_ts, category_id FROM labels WHERE active=1 AND end_ts > ? AND start_ts < ?"
                " ORDER BY created_at DESC, id DESC",
                (start, end),
            )
        ]
        hidden = [
            (r["start_ts"], r["end_ts"])
            for r in conn.execute(
                "SELECT start_ts, end_ts FROM hidden_ranges WHERE unhidden_at IS NULL AND end_ts > ? AND start_ts < ?",
                (start, end),
            )
        ]

        cats = {c.id: c for c in cat_svc.all_categories(conn)}
        resolve_cache: dict[int, int | None] = {}

        def resolve(cid: int | None) -> int | None:
            if cid is None:
                return None
            if cid not in resolve_cache:
                resolve_cache[cid] = cat_svc.resolve_id(conn, cid)
            return resolve_cache[cid]

        routine_cat: dict[str, int | None] = {}
        for r in self.routines:
            if r.category not in routine_cat:
                routine_cat[r.category] = cat_svc.resolve_name(conn, r.category)

        def overlap(a0, a1, b0, b1):
            return max(0.0, min(a1, b1) - max(a0, b0))

        loc_states: dict[int, dict] = {}
        learned: dict[int, int] = {}
        autofill = False
        if self.locator is not None:
            loc_states = {round(k): v for k, v in self.locator.window_states(conn, start, end, w).items()}
            autofill = bool(self.locator.settings_fn(conn).get("autofill_places", True))
            learned = self.locator.learned_categories(conn, w) if autofill and loc_states else {}

        candidate_idx = set(feats)
        candidate_idx.update(int((k - start) // w) for k in loc_states if start <= k < end)
        for lb in labels:
            i0 = max(0, int((lb["start_ts"] - start) // w))
            i1 = min(int((end - start) // w) - 1, int((lb["end_ts"] - start - 1e-9) // w))
            candidate_idx.update(range(i0, i1 + 1))

        out = []
        for i in sorted(candidate_idx):
            w0, w1 = start + i * w, start + (i + 1) * w
            is_hidden = any(overlap(w0, w1, h0, h1) >= w / 2 for h0, h1 in hidden)
            if is_hidden and not include_hidden:
                continue
            f = feats.get(i)
            label = next((lb for lb in labels if overlap(w0, w1, lb["start_ts"], lb["end_ts"]) >= min_overlap), None)
            loc = loc_states.get(round(w0))

            cid, source, routine_name, confidence = None, None, None, None
            if label is not None:
                cid, source, confidence = resolve(label["category_id"]), "label", 1.0
            elif f is not None and f["active_s"] >= min_active:
                r = first_match(self.routines, f)
                if r is not None and routine_cat.get(r.category) is not None:
                    cid, source, routine_name = routine_cat[r.category], "routine", r.name
                    confidence = f["signal_confidence"]
                else:
                    source = "unclassified"
                if use_model and self.model is not None:
                    served = self.model.serve(w0, f, cid)
                    if served is not None:
                        cid, source, confidence = resolve(served[0]), "model", round(served[1], 3)
                if cid is None and loc is not None:
                    lc = self.locator.fill_category(conn, loc, learned, autofill)
                    if lc is not None:
                        cid, source = resolve(lc), "location"
            else:  # no device activity: only your location can fill it
                lc = self.locator.fill_category(conn, loc, learned, autofill) if loc is not None else None
                if lc is None:
                    continue  # blank
                cid, source = resolve(lc), "location"

            c = cats.get(cid) if cid is not None else None
            out.append(
                {
                    "window_start": w0,
                    "window_end": w1,
                    "category_id": cid,
                    "category": c.name if c else None,
                    "color": c.color if c else None,
                    "source": source,
                    "routine": routine_name,
                    "label_id": label["id"] if label else None,
                    "confidence": confidence,
                    "hidden": is_hidden,
                    "features": f,
                    "location": loc,
                }
            )
        self._sessions(conn, out, w, cats, resolve)
        for o in out:  # the activity each block belongs to (old, not-yet-moved categories stand for themselves)
            c = cats.get(o["category_id"]) if o["category_id"] is not None else None
            act = cat_svc.activity_of(cats, o["category_id"])
            o["activity_id"] = act.id if act else (c.id if c else None)
            o["activity"] = act.name if act else (c.name if c else None)
            o["activity_color"] = act.color if act else (c.color if c else None)
        return out

    def _sessions(self, conn, out: list[dict], w: int, cats: dict, resolve) -> None:
        """Whole sessions: watching >= binge threshold -> Binge watching; social media >= doom threshold ->
        Doom scrolling. Blocks you labeled are never changed and they end a session."""
        sess = ((self.ui_fn(conn) or {}).get("sessions") if self.ui_fn else None) or {}
        binge_s = float(sess.get("binge_hours", 2.0)) * 3600
        doom_s = float(sess.get("doom_minutes", 30.0)) * 60
        S = cat_svc.SEP
        ids = {n: resolve(cat_svc.find_by_name(conn, n)) for n in (
            f"Watching{S}Movies & series", f"Watching{S}Random YT videos", f"Unproductive{S}Social media",
            f"Unproductive{S}Binge watching", f"Unproductive{S}Doom scrolling")}
        watch = {ids[f"Watching{S}Movies & series"], ids[f"Watching{S}Random YT videos"]} - {None}
        social = {ids[f"Unproductive{S}Social media"]} - {None}

        def apply(members: set, target_name: str, min_s: float, why: str):
            target = ids.get(target_name)
            if not members or target is None:
                return
            run: list[dict] = []

            def flush():
                if run and len(run) * w >= min_s:
                    c = cats.get(target)
                    for o in run:
                        o.update(category_id=target, category=c.name if c else None, color=c.color if c else None,
                                 source="session", routine=why)
            for o in sorted(out, key=lambda o: o["window_start"]):
                ok = o["category_id"] in members and o["source"] != "label"
                if ok and run and abs(run[-1]["window_end"] - o["window_start"]) < 1:
                    run.append(o)
                else:
                    flush()
                    run = [o] if ok else []
            flush()

        apply(watch, f"Unproductive{S}Binge watching", binge_s, f"watching session ≥ {binge_s / 3600:g} h")
        apply(social, f"Unproductive{S}Doom scrolling", doom_s, f"social media session ≥ {doom_s / 60:g} min")
