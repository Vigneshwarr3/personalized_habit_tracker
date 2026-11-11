"""URL sanitization, ingest de-duplication, auth, pause, backups."""
from __future__ import annotations

import gzip
import sqlite3

import pytest
from fastapi.testclient import TestClient

from server.app.backup import backup_now
from server.app.sanitize import sanitize_url

from .conftest import ADMIN, post, span


@pytest.mark.parametrize("raw,expected", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s&list=PL1", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?feature=share&v=abcDEF_-123", "https://m.youtube.com/watch?v=abcDEF_-123"),
    ("https://www.youtube.com/results?search_query=secret", "https://www.youtube.com/results"),
    ("https://example.com/watch?v=abcdefgh", "https://example.com/watch"),
    ("https://user:pw@mail.google.com/mail/u/0/#inbox/FMfcg", "https://mail.google.com/mail/u/0/"),
    ("https://boards.greenhouse.io/acme/jobs/1?gh_src=x&token=y", "https://boards.greenhouse.io/acme/jobs/1"),
    ("https://shop.example.com/cart;jsessionid=ABC123?x=1", "https://shop.example.com/cart"),
    ("HTTPS://WWW.Example.COM:8443/Path", "https://www.example.com:8443/Path"),
    ("http://localhost:8888/lab?token=abc", "http://localhost:8888/lab"),
    ("https://www.youtube.com/watch?v=<script>", "https://www.youtube.com/watch"),
])
def test_sanitize_url(raw, expected):
    assert sanitize_url(raw)[0] == expected


@pytest.mark.parametrize("raw", ["file:///Users/me/secret.pdf", "chrome://settings", "about:blank", "", None,
                                 "not a url", "javascript:alert(1)"])
def test_sanitize_drops_non_web(raw):
    assert sanitize_url(raw) == (None, None)


def test_domain_strips_www():
    assert sanitize_url("https://www.linkedin.com/jobs")[1] == "linkedin.com"


def test_db_never_contains_query_strings(client, cfg):
    hbs = span("10:00", 2, "Google Chrome", "x", "https://www.linkedin.com/jobs/view/1?refId=SECRET#frag")
    hbs += span("10:02", 2, "Google Chrome", "y", "https://www.youtube.com/watch?v=abcdefgh1&utm=SECRET")
    post(client, hbs)
    conn = sqlite3.connect(cfg.db_path)
    urls = [r[0] for r in conn.execute("SELECT url FROM heartbeats")]
    assert urls == ["https://www.linkedin.com/jobs/view/1", "https://www.youtube.com/watch?v=abcdefgh1"]
    assert not any("SECRET" in (u or "") for u in urls)


def test_identical_heartbeats_coalesce(client, cfg):
    res = post(client, span("10:00", 10, "Code", "a.py", step=5))  # 120 samples
    assert res["accepted"] == 120 and res["merged"] == 119
    conn = sqlite3.connect(cfg.db_path)
    (n, samples, dur), = conn.execute("SELECT COUNT(*), SUM(n_samples), SUM(end_ts-start_ts) FROM heartbeats")
    assert (n, samples, dur) == (1, 120, 600)


def test_duplicate_batch_is_ignored(client, cfg):
    hbs = span("10:00", 1, "Code", "a.py") + span("10:01", 1, "Notes", "b")
    r1 = client.post("/heartbeats", json={"batch_id": "b1", "heartbeats": hbs}).json()
    r2 = client.post("/heartbeats", json={"batch_id": "b1", "heartbeats": hbs}).json()
    assert r1["accepted"] == 24 and r2["duplicate_batch"] is True
    conn = sqlite3.connect(cfg.db_path)
    assert conn.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0] == 2


def test_future_heartbeats_rejected(client):
    import time
    r = client.post("/heartbeats", json={"heartbeats": [
        {"ts": time.time() + 86400, "device": "mac-test", "source": "mac", "app": "X", "interval_s": 5}]})
    assert r.json()["rejected"] == 1


def test_auth_required_everywhere(app):
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/categories").status_code == 401
        assert c.post("/heartbeats", json={"heartbeats": []}).status_code == 401
        assert c.get("/categories", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_device_token_scope_and_revoke(client, app, cfg):
    tok = client.post("/devices", json={"name": "pixel", "source": "android"}).json()["token"]
    with TestClient(app) as dev:
        dev.headers["Authorization"] = f"Bearer {tok}"
        hb = span("10:00", 1, "com.whatsapp", device="SPOOFED", source="android")
        assert dev.post("/heartbeats", json={"heartbeats": hb}).status_code == 200
        assert dev.get("/status").json()["paused"] is False
        assert dev.get("/categories").status_code == 403
        assert dev.post("/labels", json={"start_ts": 0, "end_ts": 1, "category": 1}).status_code == 403
        conn = sqlite3.connect(cfg.db_path)
        assert {r[0] for r in conn.execute("SELECT device FROM heartbeats")} == {"pixel"}  # token decides device
        client.post("/devices/pixel/revoke")
        assert dev.get("/status").status_code == 401
    assert client.post("/devices", json={"name": "pixel", "source": "android"}).status_code == 409


def test_pause_flag_round_trip(client):
    assert client.post("/pause", json={"paused": True, "note": "vacation"}).json()["paused"] is True
    assert client.get("/status").json()["paused"] is True
    r = client.post("/heartbeats", json={"heartbeats": span("10:00", 1, "X")})
    assert r.json()["paused"] is True  # collectors read this on every upload and stop collecting
    client.post("/pause", json={"paused": False})
    assert client.get("/status").json()["paused"] is False


def test_backup_never_overwrites(client, cfg):
    post(client, span("10:00", 1, "Code"))
    p = backup_now(cfg, label="2026-11-03")
    assert p is not None and p.exists()
    assert backup_now(cfg, label="2026-11-03") is None
    raw = gzip.decompress(p.read_bytes())
    assert raw[:16] == b"SQLite format 3\x00"


def test_admin_token_constant(client):
    assert client.get("/status").json()["you"]["role"] == "admin"
    assert ADMIN
