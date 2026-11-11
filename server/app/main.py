"""Habit Tracker API (FastAPI). Run: uvicorn --factory server.app.main:app_factory --port 8765
(no `from __future__ import annotations` here: FastAPI must resolve the local Depends aliases at runtime)"""
import json
import logging
import os
from pathlib import Path
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Annotated, Iterator, Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator

from . import auth, categories as cat_svc, kpis, views
from .backup import backup_now, start_scheduler
from .classify.model import WindowModel, label_signature
from .classify.pipeline import Classifier
from .config import REPO_ROOT, Config, load_config
from .db import connect, init_db
from .ingest import ingest
from .kg.graph import KnowledgeGraph
from . import location as loc_svc

log = logging.getLogger("ht.api")
MAX_RANGE_DAYS = 400


# ---------------------------------------------------------------- models
class HeartbeatIn(BaseModel):
    ts: float = Field(..., description="unix seconds (UTC) when the sample started")
    device: str = Field("", max_length=64)
    source: str = Field(..., pattern=r"^[a-z0-9_-]{1,20}$")  # mac | android | ios (later) ...
    app: str = Field(..., min_length=1, max_length=500)
    title: str | None = Field(None, max_length=5000)
    url: str | None = Field(None, max_length=8000)
    idle: bool = False
    media: bool = False
    interval_s: float = Field(..., gt=0, le=3600)


class HeartbeatBatch(BaseModel):
    batch_id: str | None = Field(None, max_length=128)
    heartbeats: list[HeartbeatIn]


class CategoryCreate(BaseModel):
    name: str
    color: str


class CategoryPatch(BaseModel):
    name: str | None = None
    color: str | None = None
    archived: bool | None = None


class MergeIn(BaseModel):
    source: int | str
    target: int | str


class LabelIn(BaseModel):
    start_ts: float
    end_ts: float
    category: int | str
    note: str | None = Field(None, max_length=500)

    @field_validator("end_ts")
    @classmethod
    def _order(cls, v, info):
        if "start_ts" in info.data and v <= info.data["start_ts"]:
            raise ValueError("end_ts must be after start_ts")
        return v


class LocationPoint(BaseModel):
    ts: float
    lat: float
    lon: float
    accuracy_m: float | None = None
    activity: str | None = Field(None, max_length=20)


class LocationBatch(BaseModel):
    points: list[LocationPoint] = Field(..., max_length=5000)


class PlaceIn(BaseModel):
    lat: float
    lon: float


class PlaceEditIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=60)
    category_id: int | None = None      # tag: the sub-activity for time spent here
    clear_tag: bool = False


class SubIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    color: str | None = None


class ActivityIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    color: str


class ConfirmDayIn(BaseModel):
    date: date


class HiddenIn(BaseModel):
    start_ts: float
    end_ts: float
    reason: str | None = Field(None, max_length=200)


class PauseIn(BaseModel):
    paused: bool
    note: str | None = Field(None, max_length=200)


class KGCorrectIn(BaseModel):
    node_id: int
    signal: str
    apply_to_parent: bool = False  # "apply to whole channel/site"


class DeviceIn(BaseModel):
    name: str = Field(..., pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    source: str = Field(..., pattern=r"^[a-z0-9_-]{1,20}$")


# ---------------------------------------------------------------- app factory
def create_app(cfg: Config | None = None, start_background: bool | None = None,
               kg: KnowledgeGraph | None = None) -> FastAPI:
    cfg = cfg or load_config()
    init_db(cfg)
    kg = kg or KnowledgeGraph(cfg)
    _c = connect(cfg.db_path)
    try:
        if (n := kg.retire_collapsed_search_pages(_c)):
            log.info("retired %d shared search-page classifications", n)
    finally:
        _c.close()
    model = WindowModel(cfg)
    locator = loc_svc.Locator(kpis.location_settings)
    classifier = Classifier(cfg, kg, model, locator)
    classifier.ui_fn = kpis.load_ui  # validates rules/routines at startup: bad YAML fails fast
    train_lock = threading.Lock()

    def train_model(conn) -> dict:
        with train_lock:
            return model.train(conn, classifier)

    def model_worker(stop: threading.Event) -> None:
        """Nightly (after the backup): retrain if your labels changed since the last training."""
        hour = model.s["train_hour_local"]
        while not stop.wait(900):
            try:
                now = datetime.now(ZoneInfo(cfg.timezone))
                last = model.meta.get("trained_at")
                trained_today = last and datetime.fromtimestamp(last, ZoneInfo(cfg.timezone)).date() == now.date()
                if now.hour >= hour and not trained_today:
                    conn = connect(cfg.db_path)
                    try:
                        if label_signature(conn) != model.meta.get("label_signature"):
                            meta = train_model(conn)
                            log.info("model trained: %d labeled windows, active=%s, enabled=%s", meta["n_labeled"],
                                     meta["active"], meta["enabled_categories"])
                    finally:
                        conn.close()
            except Exception:
                log.exception("model training failed")
    if start_background is None:
        start_background = os.environ.get("HT_DISABLE_BACKGROUND") != "1"
    kg_interval = float(cfg.settings.get("knowledge_graph", {}).get("worker_interval_seconds", 60))

    def kg_worker(stop: threading.Event) -> None:
        """Background: one-time backfill of items seen before phase 2, then drain the LLM queue."""
        conn = connect(cfg.db_path)
        try:
            if kg.embedder is not None:
                try:
                    kg.embedder.embed(["warm up"])  # load the model once, off the request path
                except Exception:
                    log.exception("embedding model failed to load; semantic lookups disabled")
                    kg.embedder = None
            if conn.execute("SELECT 1 FROM meta WHERE key='kg_backfilled'").fetchone() is None:
                n = kg.backfill(conn, classifier.rule_signal)
                conn.execute("INSERT INTO meta(key, value) VALUES('kg_backfilled', ?)", (str(n),))
                log.info("knowledge graph backfill: %d items", n)
            while not stop.is_set():
                try:
                    res = kg.process_queue(conn)
                    if any(res.values()):
                        log.info("kg worker: %s", res)
                except Exception:
                    log.exception("kg worker failed")
                stop.wait(kg_interval)
        finally:
            conn.close()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = threading.Event()
        if start_background:
            start_scheduler(cfg, stop)
            if kg.enabled:
                threading.Thread(target=kg_worker, args=(stop,), name="ht-kg", daemon=True).start()
            if model.s["enabled"]:
                threading.Thread(target=model_worker, args=(stop,), name="ht-model", daemon=True).start()
        yield
        stop.set()

    app = FastAPI(title="Habit Tracker", version="1.0", lifespan=lifespan)
    app.state.cfg = cfg
    app.state.classifier = classifier
    app.state.kg = kg

    # ---- dependencies
    def get_conn() -> Iterator:
        conn = connect(cfg.db_path)
        try:
            yield conn
        finally:
            conn.close()

    Conn = Annotated[object, Depends(get_conn)]

    def principal(conn: Conn, authorization: Annotated[str | None, Header()] = None,
                  tailscale_user_login: Annotated[str | None, Header()] = None,
                  sec_fetch_site: Annotated[str | None, Header()] = None) -> auth.Principal:
        token = None
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        p = auth.authenticate(conn, cfg.admin_token, token) if token else \
            auth.authenticate_owner(cfg.owner_login, tailscale_user_login, sec_fetch_site)
        if p is None:
            raise HTTPException(401, "missing or invalid token", headers={"WWW-Authenticate": "Bearer"})
        return p

    def admin(p: Annotated[auth.Principal, Depends(principal)]) -> auth.Principal:
        if p.role != "admin":
            raise HTTPException(403, "admin token required")
        return p

    Any_ = Annotated[auth.Principal, Depends(principal)]
    Admin = Annotated[auth.Principal, Depends(admin)]

    @app.exception_handler(cat_svc.CategoryError)
    async def _cat_err(_: Request, exc: cat_svc.CategoryError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    def parse_range(start: date | None, end: date | None) -> tuple[float, float, date, date]:
        today = datetime.now(ZoneInfo(cfg.timezone)).date()
        end = end or today
        start = start or end - timedelta(days=6)
        if start > end:
            raise HTTPException(400, "start must be <= end")
        if (end - start).days > MAX_RANGE_DAYS:
            raise HTTPException(400, f"range too large (max {MAX_RANGE_DAYS} days)")
        a, b = views.day_range_ts(start, end, cfg.timezone)
        return a, b, start, end

    # ---- health / status
    @app.get("/health")
    def health():
        return {"ok": True, "time": time.time()}

    def _paused(conn) -> bool:
        r = conn.execute("SELECT value FROM settings_kv WHERE key='paused'").fetchone()
        return bool(r and r["value"] == "1")

    @app.get("/status")
    def status(conn: Conn, p: Any_):
        return {"paused": _paused(conn), "server_time": time.time(), "timezone": cfg.timezone,
                "you": {"role": p.role, "device": p.device}}

    @app.post("/pause", dependencies=[])
    def pause(body: PauseIn, conn: Conn, _: Admin):
        now = time.time()
        conn.execute(
            "INSERT INTO settings_kv(key, value, updated_at) VALUES('paused', ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            ("1" if body.paused else "0", now),
        )
        conn.execute("INSERT INTO pause_events(ts, paused, note) VALUES(?,?,?)", (now, int(body.paused), body.note))
        return {"paused": body.paused}

    dist = Path(os.environ.get("HT_DIST_DIR") or (REPO_ROOT / "dist"))

    @app.get("/app/android/version")
    def android_version(_: Any_):
        """Latest Android app published by deploy.sh (the app's "Check for update" reads this)."""
        meta = dist / "habit-tracker.json"
        if not meta.exists():
            raise HTTPException(404, "no Android app published")
        return json.loads(meta.read_text())

    @app.get("/app/android/apk")
    def android_apk(_: Any_):
        apk = dist / "habit-tracker.apk"
        if not apk.exists():
            raise HTTPException(404, "no Android app published")
        return FileResponse(apk, media_type="application/vnd.android.package-archive", filename="habit-tracker.apk")

    # ---- phone location (coarse) + places
    @app.get("/location-settings")
    def location_settings(conn: Conn, _: Any_):
        ls = kpis.location_settings(conn)
        return {"precision": ls["precision"], "decimals": loc_svc.decimals_for(ls["precision"]),
                "radius_m": ls["radius_m"]}

    @app.post("/locations")
    def post_locations(batch: LocationBatch, conn: Conn, p: Any_):
        ls = kpis.location_settings(conn)
        device = p.device or "admin-upload"
        conn.execute("BEGIN IMMEDIATE")
        try:
            res = loc_svc.ingest_points(conn, device, [pt.model_dump() for pt in batch.points], ls["precision"],
                                        ls["radius_m"])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return res

    @app.post("/places/{kind}")
    def set_place(kind: str, body: PlaceIn, conn: Conn, _: Any_):
        if kind not in ("home", "work"):
            raise HTTPException(404, "only home or work can be set this way")
        return loc_svc.set_home_or_work(conn, kind, body.lat, body.lon, kpis.location_settings(conn)["precision"])

    @app.get("/places")
    def list_places(conn: Conn, _: Admin):
        return [p | {"label": loc_svc.place_label(p), "osm_url": loc_svc.osm_link(p["lat"], p["lon"]),
                     "osm_suggestion": json.loads(p["osm_suggestion"]) if p.get("osm_suggestion") else None}
                for p in loc_svc.active_places(conn)]

    @app.patch("/places/{pid}")
    def edit_place(pid: int, body: PlaceEditIn, conn: Conn, _: Admin):
        if conn.execute("SELECT 1 FROM places WHERE id=? AND active=1", (pid,)).fetchone() is None:
            raise HTTPException(404, "no such place")
        now = time.time()
        if body.name is not None:
            conn.execute("UPDATE places SET name=?, updated_at=? WHERE id=?", (body.name.strip(), now, pid))
        if body.category_id is not None:
            cat_svc.get(conn, body.category_id)
            conn.execute("UPDATE places SET category_id=?, updated_at=? WHERE id=?", (body.category_id, now, pid))
        if body.clear_tag:
            conn.execute("UPDATE places SET category_id=NULL, updated_at=? WHERE id=?", (now, pid))
        return dict(conn.execute("SELECT * FROM places WHERE id=?", (pid,)).fetchone())

    @app.post("/places/{pid}/suggest")
    def suggest_place(pid: int, conn: Conn, _: Admin):
        """'Suggest a name': asks OpenStreetMap about this place's rounded coordinates (only when you press it)."""
        p = conn.execute("SELECT * FROM places WHERE id=? AND active=1", (pid,)).fetchone()
        if p is None:
            raise HTTPException(404, "no such place")
        try:
            sug = loc_svc.osm_suggest(p["lat"], p["lon"])
        except Exception as e:
            raise HTTPException(502, f"OpenStreetMap lookup failed: {e.__class__.__name__}")
        conn.execute("UPDATE places SET osm_suggestion=? WHERE id=?", (json.dumps(sug), pid))
        return sug

    @app.get("/privacy-rules")
    def privacy_rules(_: Any_):
        """Collectors download these so sensitive titles/paths are dropped BEFORE they leave the device."""
        from .privacy import Privacy
        return Privacy.from_settings(cfg.settings).as_dict()

    # ---- heartbeats
    @app.post("/heartbeats")
    def post_heartbeats(batch: HeartbeatBatch, conn: Conn, p: Any_):
        max_batch = int(cfg.ingest.get("max_batch", 5000))
        if len(batch.heartbeats) > max_batch:
            raise HTTPException(413, f"batch too large (max {max_batch})")
        device = p.device
        if p.role == "admin":
            names = {h.device for h in batch.heartbeats}
            if len(names) != 1 or not next(iter(names)):
                raise HTTPException(400, "admin uploads must name exactly one device")
            device = next(iter(names))
        res = ingest(conn, cfg, [h.model_dump() for h in batch.heartbeats], device=device,
                     batch_id=f"{device}:{batch.batch_id}" if batch.batch_id else None)
        new_rows = res.pop("new_rows", [])
        if kg.enabled:  # knowledge-graph lookup for new items the rules don't know (never fails the upload)
            outcomes: dict[str, int] = {}
            seen: set = set()
            for row in new_rows:
                k = (row["app"], row["domain"], row["url"], row["title"])
                if k in seen or row["idle"] or classifier.rule_signal(row).confidence >= kg.min_rule_conf:
                    continue
                seen.add(k)
                try:
                    o = kg.observe(conn, row)
                    if o:
                        outcomes[o] = outcomes.get(o, 0) + 1
                except Exception:
                    log.exception("kg observe failed")
            res["kg"] = outcomes
        if p.role == "device":
            conn.execute("UPDATE devices SET last_seen=? WHERE name=?", (time.time(), device))
        return res | {"paused": _paused(conn)}

    # ---- categories
    @app.get("/categories")
    def list_categories(conn: Conn, _: Admin, include_hidden: bool = True):
        out = []
        for c in cat_svc.all_categories(conn, include_hidden=include_hidden):
            aliases = [r["alias"] for r in conn.execute(
                "SELECT alias FROM category_aliases WHERE category_id=? ORDER BY created_at", (c.id,))]
            out.append(c.as_dict() | {"resolved_id": cat_svc.resolve_id(conn, c.id), "former_names": aliases})
        return out

    @app.post("/categories", status_code=201)
    def add_category(body: CategoryCreate, conn: Conn, _: Admin):
        return cat_svc.add(conn, body.name, body.color).as_dict()

    @app.patch("/categories/{cid}")
    def patch_category(cid: int, body: CategoryPatch, conn: Conn, _: Admin):
        c = cat_svc.get(conn, cid)
        if body.name is not None:
            c = cat_svc.rename(conn, cid, body.name)
        if body.color is not None:
            c = cat_svc.recolor(conn, cid, body.color)
        if body.archived is not None:
            c = cat_svc.set_archived(conn, cid, body.archived)
        return c.as_dict()

    @app.post("/categories/activities", status_code=201)
    def add_activity(body: ActivityIn, conn: Conn, _: Admin):
        return cat_svc.add_activity(conn, body.name, body.color).as_dict()

    @app.post("/categories/{aid}/subs", status_code=201)
    def add_sub(aid: int, body: SubIn, conn: Conn, _: Admin):
        return cat_svc.add_sub(conn, aid, body.name, body.color).as_dict()

    @app.post("/categories/{cid}/follow-activity-color")
    def follow_color(cid: int, conn: Conn, _: Admin):
        return cat_svc.follow_activity_color(conn, cid).as_dict()

    @app.get("/categories/labelable")
    def labelable(conn: Conn, _: Admin):
        return [c.as_dict() for c in cat_svc.labelable(conn)]

    @app.post("/categories/merge")
    def merge_categories(body: MergeIn, conn: Conn, _: Admin):
        src = cat_svc.find_by_name(conn, body.source) if isinstance(body.source, str) and not body.source.isdigit() \
            else int(body.source)
        tgt = cat_svc.find_by_name(conn, body.target) if isinstance(body.target, str) and not body.target.isdigit() \
            else int(body.target)
        if src is None or tgt is None:
            raise HTTPException(404, "unknown category")
        return cat_svc.merge(conn, src, tgt).as_dict()

    @app.post("/categories/{cid}/unmerge")
    def unmerge_category(cid: int, conn: Conn, _: Admin):
        return cat_svc.unmerge(conn, cid).as_dict()

    @app.get("/categories/history")
    def category_history(conn: Conn, _: Admin, category_id: int | None = None):
        return cat_svc.history(conn, category_id)

    # ---- labels (corrections)
    @app.post("/labels", status_code=201)
    def add_label(body: LabelIn, conn: Conn, _: Admin):
        cid = cat_svc.resolve_ref(conn, body.category)
        cur = conn.execute(
            "INSERT INTO labels(start_ts, end_ts, category_id, note, created_at) VALUES(?,?,?,?,?)",
            (body.start_ts, body.end_ts, cid, body.note, time.time()),
        )
        return {"id": cur.lastrowid, "category_id": cid}

    @app.post("/labels/confirm-day")
    def confirm_day(body: ConfirmDayIn, conn: Conn, _: Admin):
        """'This day looks right': every classified, not-yet-labeled window becomes a confirmed label
        (training data for the model). Adjacent windows with the same category become one label."""
        a, b = views.day_range_ts(body.date, body.date, cfg.timezone)
        spans: list[list] = []
        n = 0
        for w in classifier.classify_range(conn, a, b):
            if w["category_id"] is None or w["source"] == "label":
                continue
            n += 1
            if spans and spans[-1][2] == w["category_id"] and abs(spans[-1][1] - w["window_start"]) < 1:
                spans[-1][1] = w["window_end"]
            else:
                spans.append([w["window_start"], w["window_end"], w["category_id"]])
        now = time.time()
        for s0, s1, cid in spans:
            conn.execute("INSERT INTO labels(start_ts, end_ts, category_id, note, source, created_at) "
                         "VALUES(?,?,?,?, 'confirmed', ?)", (s0, s1, cid, "confirmed day", now))
        return {"confirmed_windows": n, "labels_created": len(spans)}

    @app.get("/labels/stats")
    def label_stats(conn: Conn, _: Admin):
        by = {r[0]: int(round(r[1] / cfg.window_s)) for r in conn.execute(
            "SELECT source, COALESCE(SUM(end_ts-start_ts),0) FROM labels WHERE active=1 GROUP BY source")}
        return {"labeled_windows_approx": sum(by.values()), "by_source": by,
                "needed_for_model": model.s["min_labeled_windows"]}

    @app.post("/labels/{lid}/undo")
    def undo_label(lid: int, conn: Conn, _: Admin):
        cur = conn.execute("UPDATE labels SET active=0, deactivated_at=? WHERE id=? AND active=1", (time.time(), lid))
        if cur.rowcount == 0:
            raise HTTPException(404, "no active label with that id")
        return {"id": lid, "active": False}

    @app.get("/labels")
    def list_labels(conn: Conn, _: Admin, start: date | None = None, end: date | None = None,
                    include_inactive: bool = False):
        a, b, *_ = parse_range(start, end)
        q = "SELECT * FROM labels WHERE end_ts > ? AND start_ts < ?"
        if not include_inactive:
            q += " AND active=1"
        return [dict(r) for r in conn.execute(q + " ORDER BY start_ts", (a, b))]

    # ---- hidden ranges (data kept, just not shown)
    @app.post("/hidden-ranges", status_code=201)
    def hide_range(body: HiddenIn, conn: Conn, _: Admin):
        if body.end_ts <= body.start_ts:
            raise HTTPException(400, "end_ts must be after start_ts")
        cur = conn.execute("INSERT INTO hidden_ranges(start_ts, end_ts, reason, created_at) VALUES(?,?,?,?)",
                           (body.start_ts, body.end_ts, body.reason, time.time()))
        return {"id": cur.lastrowid}

    @app.post("/hidden-ranges/{hid}/unhide")
    def unhide_range(hid: int, conn: Conn, _: Admin):
        cur = conn.execute("UPDATE hidden_ranges SET unhidden_at=? WHERE id=? AND unhidden_at IS NULL",
                           (time.time(), hid))
        if cur.rowcount == 0:
            raise HTTPException(404, "no hidden range with that id")
        return {"id": hid, "hidden": False}

    @app.get("/hidden-ranges")
    def list_hidden(conn: Conn, _: Admin):
        return [dict(r) for r in conn.execute("SELECT * FROM hidden_ranges ORDER BY start_ts DESC")]

    # ---- classified data
    @app.get("/windows")
    def get_windows(conn: Conn, _: Admin, start: date | None = None, end: date | None = None,
                    include_hidden: bool = False):
        a, b, s, e = parse_range(start, end)
        ws = classifier.classify_range(conn, a, b, include_hidden=include_hidden)
        locs = locator.window_states(conn, a, b, cfg.window_s)  # every block with a location, even empty ones
        return {"timezone": cfg.timezone, "window_s": cfg.window_s, "start": s, "end": e, "windows": ws,
                "locations": {str(round(k)): v for k, v in locs.items()}}

    @app.get("/dashboard")
    def get_dashboard(conn: Conn, _: Admin, start: date | None = None, end: date | None = None):
        a, b, s, e = parse_range(start, end)
        ws = classifier.classify_range(conn, a, b)
        return {
            "timezone": cfg.timezone,
            "start": s,
            "end": e,
            "dates": [(s + timedelta(days=i)).isoformat() for i in range((e - s).days + 1)],
            "heatmap": views.heatmap(ws, cfg.timezone, cfg.window_s),
            "summary": views.summary(ws, cfg.window_s),
            "categories": [c.as_dict() for c in cat_svc.all_categories(conn, include_hidden=False)],
        }

    # ---- overview cards + dashboard settings (same on every device)
    @app.get("/ui-settings")
    def get_ui_settings(conn: Conn, _: Admin):
        return kpis.load_ui(conn) | {"card_labels": kpis.CARD_LABELS}

    @app.put("/ui-settings")
    def put_ui_settings(body: dict, conn: Conn, _: Admin):
        body.pop("card_labels", None)
        try:
            return kpis.save_ui(conn, body)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/kpis")
    def get_kpis(conn: Conn, _: Admin, start: date | None = None, end: date | None = None):
        _a, _b, s, e = parse_range(start, end)
        return kpis.compute(conn, classifier, s, e, kpis.load_ui(conn))

    # ---- devices & stats
    @app.get("/devices")
    def list_devices(conn: Conn, _: Admin):
        return [dict(r) for r in conn.execute(
            "SELECT name, source, created_at, revoked_at, last_seen FROM devices ORDER BY id")]

    @app.post("/devices", status_code=201)
    def add_device(body: DeviceIn, conn: Conn, _: Admin):
        if conn.execute("SELECT 1 FROM devices WHERE name=?", (body.name,)).fetchone():
            raise HTTPException(409, "device name already used (names are kept forever, pick another)")
        return {"name": body.name, "token": auth.create_device(conn, body.name, body.source),
                "note": "store this token now; it is not shown again"}

    @app.post("/devices/{name}/revoke")
    def revoke(name: str, conn: Conn, _: Admin):
        if not auth.revoke_device(conn, name):
            raise HTTPException(404, "no active device with that name")
        return {"name": name, "revoked": True}

    @app.get("/stats")
    def stats(conn: Conn, _: Admin):
        per_device = [dict(r) for r in conn.execute(
            "SELECT device, source, COUNT(*) AS spans, SUM(n_samples) AS samples, MIN(start_ts) AS first_ts,"
            " MAX(end_ts) AS last_ts FROM heartbeats GROUP BY device, source ORDER BY device")]
        size = cfg.db_path.stat().st_size if cfg.db_path.exists() else 0
        return {"devices": per_device, "db_bytes": size, "paused": _paused(conn)}

    # ---- knowledge graph (phase 2)
    @app.get("/kg/stats")
    def kg_stats(conn: Conn, _: Admin, days: int | None = None):
        since = time.time() - days * 86400 if days else None
        return kg.stats(conn, since=since)

    @app.get("/kg/review")
    def kg_review(conn: Conn, _: Admin, limit: int = 100):
        return kg.review_queue(conn, limit=min(limit, 500))

    @app.get("/kg/nodes")
    def kg_nodes(conn: Conn, _: Admin, q: str | None = None, type: str | None = None, limit: int = 200):
        return kg.browse(conn, q=q, type_=type, limit=min(limit, 1000))

    @app.get("/kg/lookups")
    def kg_lookups(conn: Conn, _: Admin, limit: int = 100):
        return kg.recent_lookups(conn, limit=min(limit, 1000))

    @app.post("/kg/correct")
    def kg_correct(body: KGCorrectIn, conn: Conn, _: Admin):
        try:
            return kg.correct(conn, body.node_id, body.signal, body.apply_to_parent)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except KeyError:
            raise HTTPException(404, "no such node")

    @app.post("/kg/edges/{edge_id}/deactivate")
    def kg_deactivate(edge_id: int, conn: Conn, _: Admin):
        if not kg.deactivate_edge(conn, edge_id):
            raise HTTPException(404, "no active classification with that id")
        return {"edge_id": edge_id, "active": False}

    @app.post("/kg/nodes/{node_id}/retry")
    def kg_retry(node_id: int, conn: Conn, _: Admin):
        kg.retry(conn, node_id)
        return {"node_id": node_id, "status": "pending"}

    @app.post("/kg/process")
    def kg_process(conn: Conn, _: Admin, limit: int = 25):
        return kg.process_queue(conn, limit=min(limit, 200))

    # ---- learned model (phase 4)
    @app.get("/model/status")
    def model_status(conn: Conn, _: Admin):
        meta = dict(model.meta)
        meta["names"] = {c.id: c.name for c in cat_svc.all_categories(conn)}
        meta["serving"] = model.active
        meta["labels_changed_since_training"] = label_signature(conn) != meta.get("label_signature")
        return meta

    @app.post("/model/train")
    def model_train(conn: Conn, _: Admin):
        return train_model(conn)

    @app.get("/signals")
    def list_signals(_: Admin):
        return {"signals": kg.allowed, "descriptions": kg.descriptions}

    @app.post("/backup")
    def backup(_: Admin):
        label = datetime.now(ZoneInfo(cfg.timezone)).strftime("%Y-%m-%d_%H%M%S")
        path = backup_now(cfg, label=label)
        return {"written": str(path) if path else None}

    # ---- the web app (React build in web/dist), served at /ui/ ----------------------------------------
    web_dir = Path(os.environ.get("HT_WEB_DIR") or (REPO_ROOT / "web" / "dist")).resolve()

    @app.get("/ui", include_in_schema=False)
    @app.get("/", include_in_schema=False)
    def ui_redirect():
        return RedirectResponse("/ui/")

    @app.get("/ui/{path:path}", include_in_schema=False)
    def ui(path: str):
        """Static files of the web app; any other path is a page of the app, so it gets index.html.
        No login needed here: the files hold no data; every data call is checked."""
        index = web_dir / "index.html"
        if not index.exists():
            raise HTTPException(404, "web app not built (run: cd web && npm run build)")
        f = (web_dir / path).resolve()
        if path and f.is_file() and f.is_relative_to(web_dir):
            # hashed build files never change; everything else is re-checked on each load
            immutable = f.parent.name == "assets"
            return FileResponse(f, headers={"Cache-Control": "public, max-age=31536000, immutable" if immutable
                                            else "no-cache"})
        if path.startswith("assets/") or path in ("sw.js", "manifest.webmanifest"):
            raise HTTPException(404, "not found")  # a missing build file must not turn into index.html
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app


def app_factory() -> FastAPI:
    """Entry point: uvicorn --factory server.app.main:app_factory"""
    logging.basicConfig(level=os.environ.get("HT_LOG_LEVEL", "INFO"))
    return create_app()
