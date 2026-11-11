"""Phase 3: the Android collector's heartbeats (package names, device token) flow through the same pipeline."""
from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from .conftest import DAY, DOOM, GL, LINKEDIN, MOVIES, local_ts, span


def _android(start, minutes, pkg, step=60, device="pixel-7a"):
    # exactly what the app sends: package name only, no title/url, idle=false (screen on + app in front)
    return [dict(h, title=None, url=None) for h in span(start, minutes, pkg, device=device, source="android", step=step)]


def test_android_phone_flow(client, app, cfg):
    tok = client.post("/devices", json={"name": "pixel-7a", "source": "android"}).json()["token"]
    with TestClient(app) as phone:
        phone.headers["Authorization"] = f"Bearer {tok}"
        hb = (_android("08:00", 30, "com.duolingo") + _android("21:00", 45, "com.instagram.android")
              + _android("12:00", 15, "com.linkedin.android"))
        r = phone.post("/heartbeats", json={"batch_id": "b1", "heartbeats": hb})
        assert r.status_code == 200 and r.json()["paused"] is False
        # the app retries the same batch after a network error -> no duplicates
        assert phone.post("/heartbeats", json={"batch_id": "b1", "heartbeats": hb}).json()["duplicate_batch"]
    ws = {w["window_start"]: w for w in client.get("/windows", params={"start": DAY, "end": DAY}).json()["windows"]}
    assert ws[local_ts("08:00")]["category"] == GL
    assert ws[local_ts("21:00")]["category"] == DOOM      # 45 min of Instagram in one go (threshold 30 min)
    assert ws[local_ts("12:00")]["category"] == LINKEDIN
    conn = sqlite3.connect(cfg.db_path)
    assert {r[0] for r in conn.execute("SELECT DISTINCT device FROM heartbeats")} == {"pixel-7a"}
    assert conn.execute("SELECT COUNT(*) FROM heartbeats WHERE title IS NOT NULL OR url IS NOT NULL").fetchone()[0] == 0


def test_phone_and_mac_in_same_window(client):
    # Mac idle (nothing sent) but phone on Duolingo -> learning; Mac on Netflix + phone on Instagram -> fun
    from .conftest import post
    post(client, span("20:00", 15, "Google Chrome", "Netflix", "https://www.netflix.com/watch/1", step=15))
    post(client, _android("20:00", 15, "com.instagram.android"), batch_id="ph")
    w = [w for w in client.get("/windows", params={"start": DAY, "end": DAY}).json()["windows"]
         if w["window_start"] == local_ts("20:00")][0]
    assert w["category"] == MOVIES      # Netflix on the Mac outweighs 15 min of Instagram
    assert set(w["features"]["devices"]) == {"mac-test", "pixel-7a"}  # both device streams merged into one window


def test_android_update_endpoints(client, app, tmp_path, monkeypatch):
    import hashlib, json
    from server.app.main import create_app
    dist = tmp_path / "dist"; dist.mkdir()
    (dist / "habit-tracker.apk").write_bytes(b"PK-fake-apk")
    (dist / "habit-tracker.json").write_text(json.dumps(
        {"version_code": 2, "version_name": "1.1", "sha256": hashlib.sha256(b"PK-fake-apk").hexdigest()}))
    monkeypatch.setenv("HT_DIST_DIR", str(dist))
    tok = client.post("/devices", json={"name": "pixel-x", "source": "android"}).json()["token"]
    app2 = create_app(app.state.cfg, start_background=False, kg=app.state.kg)
    with TestClient(app2) as phone:
        phone.headers["Authorization"] = f"Bearer {tok}"
        assert phone.get("/app/android/version").json()["version_code"] == 2
        r = phone.get("/app/android/apk")
        assert r.status_code == 200 and r.content == b"PK-fake-apk"
    with TestClient(app2) as anon:
        assert anon.get("/app/android/apk").status_code == 401
