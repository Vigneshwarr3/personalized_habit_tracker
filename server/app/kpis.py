"""Overview cards (KPIs) + the dashboard settings that control them (stored on the server, same on every device).

Cards add up time per ACTIVITY (old categories you haven't moved yet count as themselves).
"""
from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from typing import Literal

from pydantic import BaseModel, Field

from . import categories as cat_svc
from .views import day_range_ts

CARD_KEYS = ["top3", "change_vs_previous", "group_totals", "time_to_cut", "coverage", "phone_vs_laptop",
             "focus", "streak", "commute"]
CARD_LABELS = {
    "top3": "Top 3 sub-activities", "change_vs_previous": "Change vs previous period", "group_totals": "Activities",
    "time_to_cut": "Time to cut", "coverage": "Day coverage", "phone_vs_laptop": "Phone vs laptop",
    "focus": "Longest focus session", "streak": "Streak", "commute": "Commute time",
}
STREAK_LOOKBACK_DAYS = 120


class StreakRule(BaseModel):
    activity: int | None = None            # activity id (default: Learning)
    min_hours: float = Field(2.0, ge=0, le=24)


class FocusRule(BaseModel):
    activities: list[int] = []             # default: Learning
    categories: list[int] = []             # extra sub-activities


class LocationSettings(BaseModel):
    travel_km: float = Field(50.0, ge=1, le=5000)          # farther than this from home = Travel
    precision: Literal["100m", "1km"] = "100m"             # how coarse stored coordinates are
    radius_m: float = Field(150.0, ge=50, le=2000)         # size of home / work / other places
    autofill_places: bool = True                            # fill empty blocks at places you labeled twice


class SessionRules(BaseModel):
    binge_hours: float = Field(2.0, ge=0.25, le=24)        # watching this long in one go = Binge watching
    doom_minutes: float = Field(30.0, ge=5, le=600)        # social media this long in one go = Doom scrolling


class UISettings(BaseModel):
    cards: dict[str, bool]
    time_to_cut_activity: int | None = None                # default: Unproductive
    streak: StreakRule = StreakRule()
    focus: FocusRule = FocusRule()
    location: LocationSettings = LocationSettings()
    sessions: SessionRules = SessionRules()


def _activity_id(conn, name: str) -> int | None:
    cid = cat_svc.find_by_name(conn, name)
    return cid if cid is not None and cat_svc.get(conn, cid).is_activity else None


def default_ui(conn) -> dict:
    learning = _activity_id(conn, "Learning")
    return UISettings(cards={k: True for k in CARD_KEYS}, time_to_cut_activity=_activity_id(conn, "Unproductive"),
                      streak=StreakRule(activity=learning),
                      focus=FocusRule(activities=[learning] if learning else [])).model_dump()


def load_ui(conn) -> dict:
    r = conn.execute("SELECT value FROM settings_kv WHERE key='ui'").fetchone()
    if r is None:
        ui = default_ui(conn)
        save_ui(conn, ui)
        return ui
    ui = json.loads(r["value"])
    if "groups" in ui:  # settings from before activities: keep your cards, location and streak hours
        d = default_ui(conn)
        d["cards"] = ui.get("cards", d["cards"])
        d["location"] = ui.get("location") or d["location"]
        d["streak"]["min_hours"] = (ui.get("streak") or {}).get("min_hours", 2.0)
        ui = d
    ui["cards"] = {k: bool(ui.get("cards", {}).get(k, True)) for k in CARD_KEYS}  # new cards default to on
    ui["location"] = LocationSettings(**(ui.get("location") or {})).model_dump()
    ui["sessions"] = SessionRules(**(ui.get("sessions") or {})).model_dump()
    return ui


def location_settings(conn) -> dict:
    return load_ui(conn)["location"]


def save_ui(conn, ui: dict) -> dict:
    ui = UISettings(**ui).model_dump()
    acts = {c.id for c in cat_svc.all_categories(conn) if c.is_activity}
    for ref in [ui["time_to_cut_activity"], ui["streak"]["activity"], *ui["focus"]["activities"]]:
        if ref is not None and ref not in acts:
            raise ValueError(f"{ref} is not an activity")
    known = {r[0] for r in conn.execute("SELECT id FROM categories")}
    for cid in ui["focus"]["categories"]:
        if cid not in known:
            raise ValueError(f"unknown category id {cid}")
    conn.execute("INSERT INTO settings_kv(key, value, updated_at) VALUES('ui', ?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                 (json.dumps(ui), time.time()))
    return ui


def compute(conn, classifier, start: date, end: date, ui: dict, now: float | None = None) -> dict:
    cfg = classifier.cfg
    tz = ZoneInfo(cfg.timezone)
    w = cfg.window_s
    now = time.time() if now is None else now
    n_days = (end - start).days + 1
    prev_start, prev_end = start - timedelta(days=n_days), start - timedelta(days=1)
    look_start = min(prev_start, end - timedelta(days=STREAK_LOOKBACK_DAYS))
    a, b = day_range_ts(look_start, end, cfg.timezone)
    windows = classifier.classify_range(conn, a, b)

    cats = {c.id: c for c in cat_svc.all_categories(conn)}
    resolve_cache: dict[int, int | None] = {}

    def resolve(cid):
        if cid not in resolve_cache:
            resolve_cache[cid] = cat_svc.resolve_id(conn, cid)
        return resolve_cache[cid]

    def act_key(cid):  # activity of a (resolved) category; old unsorted categories stand for themselves
        a = cat_svc.activity_of(cats, cid)
        return a.id if a else cid
    by_day: dict[date, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    in_range = []
    for wd in windows:
        d = datetime.fromtimestamp(wd["window_start"], tz).date()
        if wd["category_id"] is not None:
            by_day[d][wd["category_id"]] += w / 3600
        if start <= d <= end:
            in_range.append(wd)

    def period(s: date, e: date) -> dict[int, float]:
        out: dict[int, float] = defaultdict(float)
        for d, hours in by_day.items():
            if s <= d <= e:
                for cid, h in hours.items():
                    out[cid] += h
        return out

    cur, prev = period(start, end), period(prev_start, prev_end)
    r2 = lambda x: round(x, 2)

    top3 = [{"category_id": cid, "category": cats[cid].name, "short_name": cats[cid].short_name,
             "color": cats[cid].color, "hours": r2(h), "prev_hours": r2(prev.get(cid, 0.0))}
            for cid, h in sorted(cur.items(), key=lambda kv: -kv[1])[:3] if cid in cats]
    by_act_cur: dict[int, float] = defaultdict(float)
    by_act_prev: dict[int, float] = defaultdict(float)
    for cid, h in cur.items():
        by_act_cur[act_key(cid)] += h
    for cid, h in prev.items():
        by_act_prev[act_key(cid)] += h
    group_totals = [{"group": cats[a].name, "activity_id": a, "color": cats[a].color, "hours": r2(by_act_cur.get(a, 0.0)),
                     "prev_hours": r2(by_act_prev.get(a, 0.0))}
                    for a in (set(by_act_cur) | set(by_act_prev)) if a in cats]
    group_totals.sort(key=lambda g: -g["hours"])
    ttc = ui.get("time_to_cut_activity")
    time_to_cut = None
    if ttc in cats:
        time_to_cut = {"group": cats[ttc].name, "hours": r2(by_act_cur.get(ttc, 0.0)), "prev_hours": r2(by_act_prev.get(ttc, 0.0))}

    # coverage: share of the range's 15-min blocks (up to now) that have a category
    a_rng, b_rng = day_range_ts(start, end, cfg.timezone)
    slots = max(0, math.ceil((min(b_rng, now) - a_rng) / w))  # blocks that have started
    filled = sum(1 for wd in in_range if wd["category_id"] is not None and wd["window_start"] < now)
    coverage = {"filled_blocks": filled, "total_blocks": slots,
                "percent": round(100 * filled / slots, 1) if slots else None}

    phone = laptop = 0.0
    for wd in in_range:
        f = wd["features"] or {}
        am, src = f.get("active_minutes", 0.0), f.get("source_shares", {}) or {}
        phone += am * (src.get("android", 0.0) + src.get("ios", 0.0)) / 60
        laptop += am * src.get("mac", 0.0) / 60
    phone_vs_laptop = {"phone_hours": r2(phone), "laptop_hours": r2(laptop)}

    focus_acts = set(ui["focus"]["activities"])
    focus_extra = {resolve(c) for c in ui["focus"]["categories"]} - {None}
    focus_set = {cid for cid in cats if act_key(cid) in focus_acts or cid in focus_extra}
    best = (0, None, None)
    run_start = run_end = None
    for wd in sorted(in_range, key=lambda x: x["window_start"]):
        if wd["category_id"] in focus_set:
            if run_end is not None and abs(wd["window_start"] - run_end) < 1:
                run_end = wd["window_end"]
            else:
                run_start, run_end = wd["window_start"], wd["window_end"]
            if run_end - run_start > best[0]:
                best = (run_end - run_start, run_start, run_end)
        else:
            run_start = run_end = None
    focus = {"hours": r2(best[0] / 3600), "start_ts": best[1], "end_ts": best[2],
             "date": datetime.fromtimestamp(best[1], tz).date().isoformat() if best[1] else None}

    rule = ui["streak"]
    sact = rule.get("activity")
    sgroup = {cid for cid in cats if sact is not None and act_key(cid) == sact}
    today = datetime.fromtimestamp(now, tz).date()
    last = min(end, today)

    def met(d: date) -> bool:
        return sum(h for cid, h in by_day.get(d, {}).items() if cid in sgroup) >= rule["min_hours"] and bool(sgroup)

    d = last
    today_pending = last == today and not met(today)
    if today_pending:
        d -= timedelta(days=1)
    current = 0
    while d >= look_start and met(d):
        current += 1
        d -= timedelta(days=1)
    best_streak = run = 0
    d = look_start
    while d <= last:
        run = run + 1 if met(d) else 0
        best_streak = max(best_streak, run)
        d += timedelta(days=1)
    streak = {"rule": f"{cats[sact].name if sact in cats else '—'} ≥ {rule['min_hours']:g} h/day", "current_days": current,
              "best_days": best_streak, "today_pending": today_pending, "lookback_days": STREAK_LOOKBACK_DAYS}

    commute_h = sum(w for wd in in_range if (wd.get("location") or {}).get("state") == "commute") / 3600
    commute_prev = sum(w for wd in windows if (wd.get("location") or {}).get("state") == "commute"
                       and prev_start <= datetime.fromtimestamp(wd["window_start"], tz).date() <= prev_end) / 3600
    commute = {"hours": r2(commute_h), "prev_hours": r2(commute_prev)}

    return {"start": start, "end": end, "previous": {"start": prev_start, "end": prev_end}, "cards": ui["cards"],
            "commute": commute,
            "top3": top3, "group_totals": group_totals, "time_to_cut": time_to_cut, "coverage": coverage,
            "phone_vs_laptop": phone_vs_laptop, "focus": focus, "streak": streak}
