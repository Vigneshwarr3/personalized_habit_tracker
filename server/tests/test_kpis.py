"""Overview cards: top 3 with previous period, activity totals, time to cut, coverage, phone vs laptop, focus,
streak, and the dashboard settings (server-side, validated, ids so renames keep working)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from server.app import kpis
from server.app.classify.pipeline import Classifier
from server.app.db import connect

from .conftest import GL, TZ, cat_id, local_ts, post, span


def _label(client, d, a, b, name):
    r = client.post("/labels", json={"start_ts": local_ts(a, d), "end_ts": local_ts(b, d), "category": name})
    assert r.status_code == 201


def test_ui_settings_defaults_and_validation(client):
    ui = client.get("/ui-settings").json()
    assert set(ui["cards"]) == set(kpis.CARD_KEYS) and all(ui["cards"].values())
    learning, unprod = cat_id(client, "Learning"), cat_id(client, "Unproductive")
    assert ui["time_to_cut_activity"] == unprod
    assert ui["streak"] == {"activity": learning, "min_hours": 2.0}
    assert ui["focus"] == {"activities": [learning], "categories": []}
    assert ui["sessions"] == {"binge_hours": 2.0, "doom_minutes": 30.0}
    ui["cards"]["phone_vs_laptop"] = False
    ui["focus"] = {"activities": [learning, cat_id(client, "Project")], "categories": [cat_id(client, "Exercise")]}
    ui["sessions"]["doom_minutes"] = 45
    assert client.put("/ui-settings", json=ui).status_code == 200
    again = client.get("/ui-settings").json()
    assert again["cards"]["phone_vs_laptop"] is False and again["sessions"]["doom_minutes"] == 45
    assert client.put("/ui-settings", json=dict(again, time_to_cut_activity=cat_id(client, GL))).status_code == 400
    assert client.put("/ui-settings", json=dict(again, sessions={"binge_hours": 0, "doom_minutes": 30})).status_code == 400
    client.patch(f"/categories/{learning}", json={"name": "Study"})       # ids: renames don't break settings
    assert client.get("/ui-settings").json()["streak"]["activity"] == learning


def test_kpis_cards(client, cfg):
    days = [(date(2026, 11, 1) + timedelta(days=i)).isoformat() for i in range(6)]  # Nov 1..6
    for i, d in enumerate(days):
        _label(client, d, "09:00", "11:30" if i >= 3 else "10:00", GL)            # 1 h, then 2.5 h (Nov 4-6)
        _label(client, d, "20:00", "22:00", "Watching › Movies & series")
        _label(client, d, "23:00", "23:30", "Unproductive › Negative habits")
    _label(client, days[5], "11:30", "12:30", "Learning › Scaler website")       # focus run 3.5 h on Nov 6
    post(client, span("13:00", 30, "com.duolingo", None, device="pixel", source="android", step=60, day=days[5]))
    post(client, span("14:00", 60, "Code", "x.py", step=60, day=days[5]))

    now = datetime(2026, 11, 6, 23, 59, tzinfo=ZoneInfo(TZ)).timestamp()
    conn = connect(cfg.db_path)
    k = kpis.compute(conn, Classifier(cfg), date(2026, 11, 4), date(2026, 11, 6), kpis.load_ui(conn), now=now)
    assert [(t["short_name"], t["hours"], t["prev_hours"]) for t in k["top3"]] == [
        ("General learning", 8.0, 3.0), ("Movies & series", 6.0, 6.0), ("Negative habits", 1.5, 1.5)]
    g = {x["group"]: x for x in k["group_totals"]}                   # totals per ACTIVITY
    assert g["Learning"]["hours"] == 8.0 + 1.0 + 1.0                  # + Scaler 1 h + coding 1 h (Learning › Coding)
    assert g["Watching"]["hours"] == 6.0
    assert k["time_to_cut"] == {"group": "Unproductive", "hours": 1.5, "prev_hours": 1.5}
    assert k["coverage"]["total_blocks"] == 3 * 96 and k["coverage"]["filled_blocks"] == 3 * 20 + 4 + 2 + 4
    assert k["phone_vs_laptop"] == {"phone_hours": 0.5, "laptop_hours": 1.0}
    assert k["focus"]["hours"] == 3.5 and k["focus"]["date"] == "2026-11-06"
    assert k["streak"]["current_days"] == 3 and k["streak"]["best_days"] == 3 and not k["streak"]["today_pending"]
    assert k["streak"]["rule"] == "Learning ≥ 2 h/day"


def test_kpis_endpoint(client):
    r = client.get("/kpis", params={"start": "2026-11-01", "end": "2026-11-03"})
    assert r.status_code == 200 and r.json()["top3"] == [] and r.json()["streak"]["current_days"] == 0
