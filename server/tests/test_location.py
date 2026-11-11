"""Phone location: rounding, places, and how location fills only EMPTY / UNCLASSIFIED blocks."""
from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from .conftest import DAY, local_ts, post, rotate, span

HOME = (39.76841, -86.15812)
WORK = (39.77402, -86.17604)       # ~1.6 km from home
GYM = (39.78013, -86.15021)
CHICAGO = (41.87811, -87.62980)    # ~265 km away


def pts(start, minutes, where, activity="still", day=DAY, step=300):
    t0 = local_ts(start, day)
    return [{"ts": t0 + i * step, "lat": where[0], "lon": where[1], "accuracy_m": 40, "activity": activity}
            for i in range(int(minutes * 60 // step))]


def commute(start, day=DAY):
    """30 min in a vehicle from home to work (fixes along the way)."""
    t0 = local_ts(start, day)
    out = []
    for i in range(6):
        f = i / 5
        out.append({"ts": t0 + i * 300, "lat": HOME[0] + (WORK[0] - HOME[0]) * f,
                    "lon": HOME[1] + (WORK[1] - HOME[1]) * f, "accuracy_m": 50, "activity": "in_vehicle"})
    return out


def phone(app, client):
    tok = client.post("/devices", json={"name": "pixel-6", "source": "android"}).json()["token"]
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {tok}"
    return c


def windows(client, day=DAY):
    r = client.get("/windows", params={"start": day, "end": day}).json()
    return {round(w["window_start"]): w for w in r["windows"]}, r["locations"]


def test_rounding_places_and_dedupe(client, app, cfg):
    ph = phone(app, client)
    assert ph.get("/location-settings").json() == {"precision": "100m", "decimals": 3, "radius_m": 150.0}
    batch = pts("07:00", 15, GYM) + pts("07:15", 10, WORK, "walking")
    assert ph.post("/locations", json={"points": batch}).json() == {"accepted": 5, "new_places": 1}
    assert ph.post("/locations", json={"points": batch}).json()["accepted"] == 0      # resend = no duplicates
    conn = sqlite3.connect(cfg.db_path)
    lat, lon = conn.execute("SELECT lat, lon FROM locations LIMIT 1").fetchone()
    assert (lat, lon) == (39.78, -86.15)                                             # ~100 m, rounded on arrival
    assert conn.execute("SELECT COUNT(*) FROM places WHERE kind='other'").fetchone()[0] == 1  # walking != a place
    # home / work: saving again keeps the old one in history
    ph.post("/places/home", json={"lat": 1.0, "lon": 1.0})
    ph.post("/places/home", json={"lat": HOME[0], "lon": HOME[1]})
    assert conn.execute("SELECT COUNT(*), SUM(active) FROM places WHERE kind='home'").fetchone() == (2, 1)
    assert ph.get("/places").status_code == 403                                      # listing is admin-only


def test_location_fills_only_empty_or_unclassified(client, app):
    ph = phone(app, client)
    ph.post("/places/home", json={"lat": HOME[0], "lon": HOME[1]})
    ph.post("/places/work", json={"lat": WORK[0], "lon": WORK[1]})
    loc = (pts("07:00", 60, HOME) + commute("08:00") + pts("08:30", 150, WORK)
           + pts("18:00", 60, CHICAGO))
    ph.post("/locations", json={"points": loc})
    # laptop: studying at the office 10:00-11:00; phone: Instagram on the bus 08:15-08:30
    post(client, rotate("10:00", 60, [("GoodNotes", "Notes", None), ("Code", "hw.py", None)], step=60))
    post(client, span("08:15", 15, "com.instagram.android", None, device="pixel-6", source="android", step=60))

    ws, locs = windows(client)
    cat = lambda t: (ws[local_ts(t)]["category"], ws[local_ts(t)]["source"], ws[local_ts(t)]["location"]["label"])
    assert cat("08:00") == ("Everyday activities › Commute", "location", "Commute")   # on the bus, phone idle
    assert cat("08:15") == ("Unproductive › Social media", "routine", "Commute")      # Instagram on the bus: activity wins
    assert cat("09:00") == ("Work › Office work", "location", "Work")                 # at work, nothing on devices
    assert cat("10:00") == ("Learning › General learning", "routine", "Work")         # studying at the office stays learning
    assert cat("18:00") == ("Travel › Tour (different city)", "location", "Travel")   # 265 km from home
    assert local_ts("07:00") not in ws                                           # at home: no category...
    assert locs[str(round(local_ts("07:00")))]["label"] == "Home"                # ...but shown as the location

    k = client.get("/kpis", params={"start": DAY, "end": DAY}).json()
    assert k["commute"]["hours"] == 0.5 and k["cards"]["commute"] is True       # counts the Instagram block too


def test_learned_places_and_settings(client, app):
    ph = phone(app, client)
    ph.post("/places/home", json={"lat": HOME[0], "lon": HOME[1]})
    for d in ("2026-11-01", "2026-11-02"):
        ph.post("/locations", json={"points": pts("17:00", 60, GYM, day=d)})
        client.post("/labels", json={"start_ts": local_ts("17:00", d), "end_ts": local_ts("18:00", d), "category": "Exercise"})
    ph.post("/locations", json={"points": pts("17:00", 60, GYM)})                  # DAY = Nov 3, not labeled
    ws, locs = windows(client)
    assert ws[local_ts("17:00")]["category"] == "Exercise" and ws[local_ts("17:00")]["source"] == "location"
    gym = [p for p in client.get("/places").json() if p["kind"] == "other"][0]
    client.patch(f"/places/{gym['id']}", json={"name": "Gym"})
    assert windows(client)[1][str(round(local_ts("17:00")))]["label"] == "Gym"

    ui = client.get("/ui-settings").json()
    ui["location"]["autofill_places"] = False
    client.put("/ui-settings", json=ui)
    assert local_ts("17:00") not in windows(client)[0]                           # auto-fill off -> blank again

    ph.post("/locations", json={"points": pts("20:00", 30, CHICAGO)})
    assert windows(client)[1][str(round(local_ts("20:00")))]["state"] == "travel"
    ui["location"] |= {"travel_km": 300, "precision": "1km"}
    assert client.put("/ui-settings", json=ui).status_code == 200
    assert windows(client)[1][str(round(local_ts("20:00")))]["state"] != "travel"  # 265 km < 300 km now
    assert ph.get("/location-settings").json()["decimals"] == 2
    bad = dict(ui, location=dict(ui["location"], precision="10m"))
    assert client.put("/ui-settings", json=bad).status_code == 400


KROGER_A = (39.79013, -86.14021)
KROGER_B = (39.70111, -86.20333)   # another Kroger, ~11 km away


def test_place_tags_and_same_name_inheritance(client, app, monkeypatch):
    ph = phone(app, client)
    ph.post("/places/home", json={"lat": HOME[0], "lon": HOME[1]})
    ph.post("/locations", json={"points": pts("12:00", 30, KROGER_A) + pts("18:00", 30, KROGER_B)})
    places = {p["id"]: p for p in client.get("/places").json() if p["kind"] == "other"}
    a, b = sorted(places)
    assert places[a]["osm_url"].startswith("https://www.openstreetmap.org/?mlat=")
    shopping = next(c["id"] for c in client.get("/categories").json() if c["name"] == "Everyday activities › Shopping")
    client.patch(f"/places/{a}", json={"name": "Kroger", "category_id": shopping})   # tag one Kroger...
    client.patch(f"/places/{b}", json={"name": "kroger"})                            # ...the other just gets the name
    ws, locs = windows(client)
    assert ws[local_ts("12:00")]["category"] == "Everyday activities › Shopping"
    assert ws[local_ts("18:00")]["category"] == "Everyday activities › Shopping"     # inherited by name
    client.patch(f"/places/{a}", json={"clear_tag": True})
    assert local_ts("18:00") not in windows(client)[0]

    from server.app import location as loc_svc
    monkeypatch.setattr(loc_svc, "osm_suggest", lambda lat, lon: {"name": "Kroger", "type": "supermarket",
                                                                   "address": "1 Main St, Indianapolis"})
    assert client.post(f"/places/{a}/suggest").json()["name"] == "Kroger"
    assert {p["id"]: p for p in client.get("/places").json()}[a]["osm_suggestion"]["type"] == "supermarket"


def test_doom_threshold_from_settings(client, app):
    ph = phone(app, client)
    ph.post("/heartbeats", json={"heartbeats": [dict(h, title=None) for h in span(
        "21:00", 45, "com.instagram.android", None, device="pixel-6", source="android", step=60)]})
    assert windows(client)[0][local_ts("21:00")]["category"] == "Unproductive › Doom scrolling"
    ui = client.get("/ui-settings").json()
    ui["sessions"]["doom_minutes"] = 60
    client.put("/ui-settings", json=ui)
    assert windows(client)[0][local_ts("21:00")]["category"] == "Unproductive › Social media"
