"""Turns classified windows into the dashboard's two views: hour x day grid and per-category totals."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

UNCLASSIFIED = "Unclassified"


def day_range_ts(start: date, end: date, tz: str) -> tuple[float, float]:
    """Local dates [start, end] inclusive -> UTC timestamps [start 00:00, end+1 00:00)."""
    z = ZoneInfo(tz)
    a = datetime.combine(start, time(0), z).timestamp()
    b = datetime.combine(end + timedelta(days=1), time(0), z).timestamp()
    return a, b


def heatmap(windows: list[dict], tz: str, window_s: int) -> list[dict]:
    z = ZoneInfo(tz)
    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for wdw in windows:
        local = datetime.fromtimestamp(wdw["window_start"], z)
        groups[(local.date().isoformat(), local.hour)].append(wdw)

    cells = []
    for (day, hour), ws in sorted(groups.items()):
        count: dict = defaultdict(int)
        active: dict = defaultdict(float)
        meta: dict = {}
        devices: set = set()
        places: list = []
        subs: dict = {}
        for wdw in ws:
            devices.update((wdw["features"] or {}).get("devices", []))
            lab = (wdw.get("location") or {}).get("label")
            if lab and lab not in places:
                places.append(lab)
            # cells are colored by ACTIVITY; the hover lists the sub-activities
            key = wdw.get("activity_id") if wdw.get("activity_id") is not None else UNCLASSIFIED
            count[key] += 1
            active[key] += (wdw["features"] or {}).get("active_s", 0.0)
            meta[key] = (wdw.get("activity") or UNCLASSIFIED, wdw.get("activity_color"))
            sub = wdw["category"] or UNCLASSIFIED
            subs[sub] = subs.get(sub, 0) + window_s // 60
        best = max(count, key=lambda k: (count[k], active[k]))
        name, color = meta[best]
        cells.append(
            {
                "date": day,
                "hour": hour,
                "category_id": best if best != UNCLASSIFIED else None,
                "category": name,
                "color": color,
                "minutes": subs,
                "devices": sorted(devices),
                "places": places,
            }
        )
    return cells


def summary(windows: list[dict], window_s: int) -> dict:
    hours: dict = defaultdict(float)
    meta: dict = {}
    unclassified = 0.0
    for wdw in windows:
        h = window_s / 3600
        if wdw["category_id"] is None:
            unclassified += h
            continue
        hours[wdw["category_id"]] += h
        meta[wdw["category_id"]] = (wdw["category"], wdw["color"], wdw.get("activity_id"), wdw.get("activity"),
                                    wdw.get("activity_color"))
    total = sum(hours.values())
    rows = [
        {
            "category_id": cid,
            "category": meta[cid][0],
            "color": meta[cid][1],
            "hours": round(h, 2),
            "percent": round(100 * h / total, 1) if total else 0.0,
            "label": f"{round(h, 2):g} Hours of {meta[cid][0]}, {round(100 * h / total) if total else 0}%",
            "activity_id": meta[cid][2], "activity": meta[cid][3], "activity_color": meta[cid][4],
        }
        for cid, h in sorted(hours.items(), key=lambda kv: -kv[1])
    ]
    return {"categories": rows, "classified_hours": round(total, 2), "unclassified_hours": round(unclassified, 2)}
