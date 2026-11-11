"""Sensitive activity (banks, password managers, sign-in pages): only app name + bare site is stored,
nothing goes to the knowledge graph / LLM, and the time still counts."""
from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from server.app.kg.embed import HashEmbedder
from server.app.kg.graph import KnowledgeGraph
from server.app.main import create_app

from .conftest import ADMIN, DAY, local_ts, post, span
from .test_knowledge_graph import FakeLLM


def _client(cfg):
    llm = FakeLLM({"": ("other", 0.9)})
    kg = KnowledgeGraph(cfg, provider=llm, embedder=HashEmbedder(), channel_fetcher=None)
    c = TestClient(create_app(cfg, start_background=False, kg=kg))
    c.headers["Authorization"] = f"Bearer {ADMIN}"
    return c, llm


def _rows(cfg):
    return sqlite3.connect(cfg.db_path).execute("SELECT app, title, url FROM heartbeats ORDER BY start_ts").fetchall()


def test_bank_site_keeps_only_bare_site_and_still_counts(cfg):
    c, llm = _client(cfg)
    post(c, span("10:00", 15, "Google Chrome", "Chase - Account ending 4821 - Statements",
                 "https://secure.chase.com/web/auth/dashboard/accounts/4821/statements", step=15))
    assert _rows(cfg) == [("Google Chrome", None, "https://secure.chase.com/")]
    w = c.get("/windows", params={"start": DAY, "end": DAY}).json()["windows"][0]
    assert w["category"] == "Everyday activities › Bank/Hospital/Others"   # chase.com -> admin rule still works


def test_unknown_bank_and_password_manager_never_reach_kg_or_llm(cfg):
    c, llm = _client(cfg)
    post(c, span("10:00", 5, "Google Chrome", "My Credit Union - Home", "https://www.mycitybank.example/home", step=15))
    post(c, span("10:05", 5, "1Password 8", "Personal vault - Gmail login", step=15))
    post(c, span("10:10", 5, "com.examplebank.mobile", None, device="pixel", source="android", step=30))
    c.post("/kg/process")
    assert llm.calls == []
    assert sqlite3.connect(cfg.db_path).execute("SELECT COUNT(*) FROM kg_nodes").fetchone()[0] == 0
    rows = _rows(cfg)
    assert ("1Password 8", None, None) in rows
    assert ("Google Chrome", None, "https://www.mycitybank.example/") in rows


def test_sensitive_titles_on_ordinary_sites(cfg):
    c, llm = _client(cfg)
    post(c, span("10:00", 2, "Google Chrome", "Sign in to GitHub", "https://github.com/login", step=10))
    post(c, span("10:02", 2, "Google Chrome", "Enter your verification code", "https://shop.example/checkout/otp", step=10))
    post(c, span("10:04", 2, "Google Chrome", "How to build a login page - YouTube",
                 "https://www.youtube.com/watch?v=abcdefgh1", step=10))
    rows = _rows(cfg)
    assert rows[0] == ("Google Chrome", None, "https://github.com/")
    assert rows[1] == ("Google Chrome", None, "https://shop.example/")
    assert rows[2][1] == "How to build a login page - YouTube"  # video titles are not treated as sensitive


def test_privacy_rules_endpoint_for_devices(client, app):
    tok = client.post("/devices", json={"name": "mac2", "source": "mac"}).json()["token"]
    with TestClient(app) as dev:
        r = dev.get("/privacy-rules", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    rules = r.json()
    assert "*bank*" in rules["private_domains"] and "Bitwarden" in rules["private_apps"]
    assert "password" in rules["private_title_regex"]
