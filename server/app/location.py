"""Phone location (coarse, ~100 m) -> a location state per 15-minute block, and the category fill rules.

States: home | work | commute | travel | place (a named / unnamed spot you stay at) | unknown
Fill rules (only for blocks that are still EMPTY or UNCLASSIFIED; your labels and device activity win):
  work -> Office/Work · commute -> Commute · travel -> Travel/Visiting places
  place you labeled the same way at least twice -> that category (if auto-fill is on) · home -> nothing
Coordinates never go to the LLM. Nothing here is ever deleted (places are deactivated / renamed only).
"""
from __future__ import annotations

import math
import time
from collections import Counter, defaultdict

from . import categories as cat_svc

LOCATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS locations (
    id          INTEGER PRIMARY KEY,
    device      TEXT NOT NULL,
    ts          REAL NOT NULL,
    lat         REAL NOT NULL,
    lon         REAL NOT NULL,
    accuracy_m  REAL,
    activity    TEXT,               -- in_vehicle | on_bicycle | walking | running | still | unknown
    received_at REAL NOT NULL,
    UNIQUE(device, ts)
);
CREATE INDEX IF NOT EXISTS idx_locations_ts ON locations(ts);
CREATE TABLE IF NOT EXISTS places (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL,       -- home | work | other
    name       TEXT,
    lat        REAL NOT NULL,
    lon        REAL NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""

MOVING_ACTIVITIES = {"in_vehicle", "on_bicycle"}
FOOT_ACTIVITIES = {"walking", "running", "on_foot"}
STATE_CATEGORY = {"work": "Work › Office work", "commute": "Everyday activities › Commute",
                  "travel": "Travel › Tour (different city)"}
CARRY_S = 20 * 60          # a fix stays valid for the next ~20 min if no newer one exists
MOVE_M = 500               # two fixes this far apart = you were moving
FOOT_MOVE_M = 300          # walking only counts as commuting if you actually went somewhere

DEFAULT_LOCATION_SETTINGS = {"travel_km": 50.0, "precision": "100m", "radius_m": 150.0, "autofill_places": True}


def add_columns(conn) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(places)")}
    if "category_id" not in cols:   # the sub-activity you tagged this place with
        conn.execute("ALTER TABLE places ADD COLUMN category_id INTEGER REFERENCES categories(id)")
    if "osm_suggestion" not in cols:
        conn.execute("ALTER TABLE places ADD COLUMN osm_suggestion TEXT")


def osm_link(lat: float, lon: float) -> str:
    return f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=19/{lat}/{lon}"


def osm_suggest(lat: float, lon: float, client=None) -> dict:
    """Ask OpenStreetMap what is at this (rounded) spot. Only runs when you press the button; sends only
    these coordinates, with a generic app name (no personal details)."""
    import httpx
    c = client or httpx.Client(timeout=10, headers={"User-Agent": "habit-tracker-selfhosted/1.0 (personal use)"})
    r = c.get("https://nominatim.openstreetmap.org/reverse",
              params={"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 18, "addressdetails": 1})
    r.raise_for_status()
    j = r.json()
    a = j.get("address") or {}
    street = " ".join(x for x in (a.get("house_number"), a.get("road")) if x)
    return {"name": j.get("name") or None, "type": j.get("type") or j.get("category"),
            "address": ", ".join(x for x in (street, a.get("city") or a.get("town") or a.get("village")) if x),
            "display_name": j.get("display_name")}


def decimals_for(precision: str) -> int:
    return 2 if precision == "1km" else 3   # 3 decimals ~ 110 m, 2 decimals ~ 1.1 km


def round_coord(v: float, precision: str) -> float:
    return round(float(v), decimals_for(precision))


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ------------------------------------------------------------------ storage
def ingest_points(conn, device: str, points: list[dict], precision: str, radius_m: float,
                  now: float | None = None) -> dict:
    """Store fixes (re-rounded here too). A spot where you stay that matches no known place becomes an
    'Unnamed place #N' you can name later."""
    now = time.time() if now is None else now
    added = new_places = 0
    places = active_places(conn)
    for p in sorted(points, key=lambda p: p["ts"]):
        if not (-90 <= p["lat"] <= 90 and -180 <= p["lon"] <= 180) or p["ts"] > now + 300:
            continue
        lat, lon = round_coord(p["lat"], precision), round_coord(p["lon"], precision)
        activity = (p.get("activity") or "unknown")[:20]
        cur = conn.execute(
            "INSERT OR IGNORE INTO locations(device, ts, lat, lon, accuracy_m, activity, received_at) VALUES(?,?,?,?,?,?,?)",
            (device, float(p["ts"]), lat, lon, p.get("accuracy_m"), activity, now))
        added += cur.rowcount
        if cur.rowcount and activity not in MOVING_ACTIVITIES | FOOT_ACTIVITIES:
            if not any(haversine_m(lat, lon, pl["lat"], pl["lon"]) <= radius_m for pl in places):
                c = conn.execute("INSERT INTO places(kind, name, lat, lon, created_at, updated_at) "
                                 "VALUES('other', NULL, ?, ?, ?, ?)", (lat, lon, now, now))
                places.append({"id": c.lastrowid, "kind": "other", "name": None, "lat": lat, "lon": lon})
                new_places += 1
    return {"accepted": added, "new_places": new_places}


def set_home_or_work(conn, kind: str, lat: float, lon: float, precision: str) -> dict:
    now = time.time()
    conn.execute("UPDATE places SET active=0, updated_at=? WHERE kind=? AND active=1", (now, kind))  # history kept
    cur = conn.execute("INSERT INTO places(kind, name, lat, lon, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                       (kind, kind.capitalize(), round_coord(lat, precision), round_coord(lon, precision), now, now))
    return {"id": cur.lastrowid, "kind": kind}


def active_places(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM places WHERE active=1 ORDER BY id")]


def place_label(p: dict) -> str:
    return p["name"] or f"Unnamed place #{p['id']}"


# ------------------------------------------------------------------ per-window state
class Locator:
    def __init__(self, settings_fn):
        self.settings_fn = settings_fn    # conn -> location settings dict (from the dashboard Settings)

    def _nearest(self, places, lat, lon, radius):
        best = None
        for pl in places:
            d = haversine_m(lat, lon, pl["lat"], pl["lon"])
            if d <= radius and (best is None or d < best[0]):
                best = (d, pl)
        return best[1] if best else None

    def window_states(self, conn, start: float, end: float, window_s: int) -> dict[float, dict]:
        """{window_start: state} for every window in [start, end) that has location data."""
        s = self.settings_fn(conn)
        radius, travel_m = float(s["radius_m"]), float(s["travel_km"]) * 1000
        pts = [dict(r) for r in conn.execute(
            "SELECT ts, lat, lon, activity FROM locations WHERE ts >= ? AND ts < ? ORDER BY ts", (start - CARRY_S, end))]
        if not pts:
            return {}
        places = active_places(conn)
        home = next((p for p in places if p["kind"] == "home"), None)
        tag_by_name = {}  # a tag on one "Kroger" counts for every place you named "Kroger"
        for pl in places:
            if pl.get("category_id") and pl.get("name"):
                tag_by_name.setdefault(pl["name"].strip().lower(), pl["category_id"])
        out: dict[float, dict] = {}
        w0 = math.floor(start / window_s) * window_s
        while w0 < end:
            w1 = w0 + window_s
            inwin = [p for p in pts if w0 <= p["ts"] < w1]
            before = [p for p in pts if w0 - CARRY_S <= p["ts"] < w0]
            ctx = (before[-1:] + inwin) if before else inwin
            if not ctx:
                w0 = w1
                continue
            moving = any(p["activity"] in MOVING_ACTIVITIES for p in inwin)
            for a, b in zip(ctx, ctx[1:]):
                d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
                if d >= MOVE_M or (d >= FOOT_MOVE_M and (b["activity"] in FOOT_ACTIVITIES)):
                    moving = True
            last = ctx[-1]
            far = home is not None and haversine_m(last["lat"], last["lon"], home["lat"], home["lon"]) > travel_m
            st: dict = {"lat": last["lat"], "lon": last["lon"]}
            if far:
                st |= {"state": "travel", "label": "Travel"}
            elif moving:
                st |= {"state": "commute", "label": "Commute"}
            else:
                pl = self._nearest(places, last["lat"], last["lon"], radius)
                if pl is not None:
                    tag = pl.get("category_id") or tag_by_name.get((pl.get("name") or "").strip().lower())
                    if tag:
                        st["tag_category"] = tag
                if pl is None:
                    st |= {"state": "unknown", "label": "Unknown place"}
                elif pl["kind"] in ("home", "work"):
                    st |= {"state": pl["kind"], "label": place_label(pl), "place_id": pl["id"]}
                else:
                    st |= {"state": "place", "label": place_label(pl), "place_id": pl["id"]}
            out[w0] = st
            w0 = w1
        return out

    def learned_categories(self, conn, window_s: int) -> dict[int, int]:
        """place_id -> category: places where you labeled at least 2 blocks, >= 80% with the same category."""
        s = self.settings_fn(conn)
        places = [p for p in active_places(conn) if p["kind"] == "other"]
        if not places:
            return {}
        votes: dict[int, Counter] = defaultdict(Counter)
        labels = conn.execute("SELECT start_ts, end_ts, category_id FROM labels WHERE active=1").fetchall()
        for lb in labels:
            pts = conn.execute("SELECT ts, lat, lon FROM locations WHERE ts >= ? AND ts < ?",
                               (lb["start_ts"], lb["end_ts"])).fetchall()
            seen = set()
            for p in pts:
                win = math.floor(p["ts"] / window_s)
                pl = self._nearest(places, p["lat"], p["lon"], float(s["radius_m"]))
                if pl is not None and (pl["id"], win) not in seen:
                    seen.add((pl["id"], win))
                    votes[pl["id"]][cat_svc.resolve_id(conn, lb["category_id"])] += 1
        out = {}
        for pid, c in votes.items():
            cid, n = c.most_common(1)[0]
            if n >= 2 and n / sum(c.values()) >= 0.8:
                out[pid] = cid
        return out

    def fill_category(self, conn, state: dict | None, learned: dict[int, int], autofill: bool) -> int | None:
        """Category for an EMPTY/UNCLASSIFIED block from its location state (or None)."""
        if not state:
            return None
        if state.get("tag_category"):  # your tag on this place (or a place with the same name) always applies
            return state["tag_category"]
        name = STATE_CATEGORY.get(state["state"])
        if name:
            return cat_svc.resolve_name(conn, name)
        if state["state"] == "place" and autofill:
            return learned.get(state.get("place_id"))
        return None
