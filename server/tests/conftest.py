from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

os.environ["HT_DISABLE_BACKGROUND"] = "1"

from server.app.config import load_config  # noqa: E402
from server.app.main import create_app  # noqa: E402

ADMIN = "test-admin-token"
TZ = "America/Indiana/Indianapolis"
DAY = "2026-11-03"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("HT_ADMIN_TOKEN", ADMIN)
    c = load_config(db_path=tmp_path / "t.db")
    c.settings = dict(c.settings) | {"timezone": TZ, "backup": {"dir": str(tmp_path / "backups"), "hour_local": 3},
                                     "model": dict(c.settings.get("model", {}) or {}, dir=str(tmp_path / "model"))}
    return c


@pytest.fixture
def kg(cfg):
    """Offline knowledge graph: hash embeddings, no LLM, no YouTube lookups (tests never hit the network)."""
    from server.app.kg.embed import HashEmbedder
    from server.app.kg.graph import KnowledgeGraph
    return KnowledgeGraph(cfg, provider=None, embedder=HashEmbedder(), channel_fetcher=None)


@pytest.fixture
def app(cfg, kg):
    return create_app(cfg, start_background=False, kg=kg)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {ADMIN}"
        yield c


def local_ts(hhmm: str, day: str = DAY) -> float:
    return datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=ZoneInfo(TZ)).timestamp()


def span(start: str, minutes: float, app: str, title=None, url=None, step=5, idle=False, media=False,
         device="mac-test", source="mac", day=DAY) -> list[dict]:
    t0 = local_ts(start, day)
    n = int(minutes * 60 // step)
    return [
        {"ts": t0 + i * step, "device": device, "source": source, "app": app, "title": title, "url": url,
         "idle": idle, "media": media, "interval_s": step}
        for i in range(n)
    ]


def rotate(start: str, minutes: float, items: list[tuple], every_min: float = 3, **kw) -> list[dict]:
    """Switch between several (app, title, url) contexts every `every_min` minutes."""
    out = []
    t = 0.0
    i = 0
    h, m = map(int, start.split(":"))
    base = h * 60 + m
    while t < minutes:
        dur = min(every_min, minutes - t)
        cur = base + t
        hhmm = f"{int(cur // 60):02d}:{int(cur % 60):02d}:{int(round((cur * 60) % 60)):02d}"
        app, title, url = items[i % len(items)]
        out += span(hhmm, dur, app, title, url, **kw)
        t += dur
        i += 1
    return out


def synthetic_day() -> list[dict]:
    hb: list[dict] = []
    # 09:00-11:00 learning: GoodNotes + YouTube lecture + learning site + VS Code
    hb += rotate("09:00", 120, [
        ("GoodNotes", "Algorithms notes", None),
        ("Google Chrome", "MIT 6.006 Lecture 3: Sorting - YouTube",
         "https://www.youtube.com/watch?v=Nz1KZXbghj8&t=120s&list=PLabc"),
        ("Google Chrome", "Dynamic Programming | Coursera", "https://www.coursera.org/learn/algorithms/lecture/xyz"),
        ("Code", "solution.py — algos", None),
    ])
    # 11:00-12:00 job applications on several ATS pages
    hb += rotate("11:00", 60, [
        ("Google Chrome", "Job Application for Data Scientist Intern at Acme",
         "https://boards.greenhouse.io/acme/jobs/123?gh_src=abc&utm_source=linkedin"),
        ("Google Chrome", "Software Engineer Intern - Globex", "https://jobs.lever.co/globex/abc-123/apply"),
        ("Google Chrome", "(3) Jobs | LinkedIn", "https://www.linkedin.com/jobs/view/999?refId=secret-token"),
        ("Google Chrome", "Careers", "https://acme.wd5.myworkdayjobs.com/en-US/External/job/xyz"),
    ], every_min=4)
    # 14:00-16:00 movie: one site, no keyboard/mouse input, media playing (agent reports idle=False, media=True)
    hb += span("14:00", 120, "Google Chrome", "Netflix", "https://www.netflix.com/watch/81234567?trackId=999",
               step=15, media=True)
    # 16:00-16:30 away from keyboard, nothing playing -> idle
    hb += span("16:00", 30, "Google Chrome", "Netflix", "https://www.netflix.com/browse", step=15, idle=True)
    # 20:00-20:30 YouTube entertainment
    hb += span("20:00", 30, "Google Chrome", "My weekend trip vlog - YouTube",
               "https://www.youtube.com/watch?v=abcdEFGH123&feature=share", step=10)
    return hb


def post(client, hbs: list[dict], batch_id=None, chunk=2000):
    for i in range(0, len(hbs), chunk):
        bid = f"{batch_id}-{i}" if batch_id else None
        r = client.post("/heartbeats", json={"batch_id": bid, "heartbeats": hbs[i:i + chunk]})
        assert r.status_code == 200, r.text
    return r.json()


def cat_id(client, name: str) -> int:
    """Category id by (current) name, e.g. 'Learning › General learning'."""
    return next(c["id"] for c in client.get("/categories").json() if c["name"] == name)


GL = "Learning › General learning"
CAREERS = "Career › Company careers pages"
LINKEDIN = "Career › LinkedIn"
MOVIES = "Watching › Movies & series"
RANDOM_YT = "Watching › Random YT videos"
BINGE = "Unproductive › Binge watching"
SOCIAL = "Unproductive › Social media"
DOOM = "Unproductive › Doom scrolling"
