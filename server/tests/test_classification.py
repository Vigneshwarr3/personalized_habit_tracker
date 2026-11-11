"""Synthetic day-long streams -> movie vs learning vs job-application windows; YouTube split; idle handling."""
from __future__ import annotations

from collections import Counter

from server.app.classify.rules import RuleEngine
from server.app.config import load_config

from .conftest import BINGE, CAREERS, DAY, GL, MOVIES, RANDOM_YT, local_ts, post, span, synthetic_day


def _windows(client, day=DAY):
    r = client.get("/windows", params={"start": day, "end": day})
    assert r.status_code == 200, r.text
    return r.json()["windows"]


def _cats_between(ws, start, end):
    a, b = local_ts(start), local_ts(end)
    return Counter(w["category"] for w in ws if a <= w["window_start"] < b)


def test_full_day_classification(client):
    post(client, synthetic_day(), batch_id="day")
    ws = _windows(client)

    assert _cats_between(ws, "09:00", "11:00") == {GL: 8}
    assert _cats_between(ws, "11:00", "12:00") == {CAREERS: 4}      # Greenhouse, Lever, LinkedIn, Workday mixed
    assert _cats_between(ws, "14:00", "16:00") == {BINGE: 8}        # 2 h of Netflix in one go = binge (whole session)
    assert _cats_between(ws, "16:00", "16:30") == Counter()  # idle -> blank
    assert _cats_between(ws, "20:00", "20:30") == {RANDOM_YT: 2}
    assert {w["activity"] for w in ws if w["category"] == BINGE} == {"Unproductive"}

    learning = [w for w in ws if local_ts("09:00") <= w["window_start"] < local_ts("11:00")]
    assert all(w["features"]["num_contexts"] >= 2 for w in learning)
    movie = [w for w in ws if local_ts("14:00") <= w["window_start"] < local_ts("16:00")]
    assert all(w["features"]["largest_context_share"] >= 0.99 for w in movie)
    assert all(w["features"]["run_minutes"] >= 119 for w in movie)


def test_dashboard_heatmap_and_pie(client):
    post(client, synthetic_day(), batch_id="day")
    d = client.get("/dashboard", params={"start": DAY, "end": DAY}).json()
    cells = {(c["date"], c["hour"]): c for c in d["heatmap"]}
    assert cells[(DAY, 9)]["category"] == "Learning" and cells[(DAY, 9)]["color"] == "#A5A5A5"  # colored by activity
    assert cells[(DAY, 9)]["minutes"] == {GL: 60}                                                  # hover: sub-activities
    assert cells[(DAY, 11)]["category"] == "Career"
    assert cells[(DAY, 15)]["category"] == "Unproductive"
    assert (DAY, 16) not in cells  # idle hour is blank
    s = {r["category"]: r for r in d["summary"]["categories"]}
    assert s[GL]["hours"] == 2.0 and s[GL]["activity"] == "Learning"
    assert s[CAREERS]["hours"] == 1.0
    assert s[BINGE]["hours"] == 2.0 and s[RANDOM_YT]["hours"] == 0.5
    assert s[GL]["label"] == f"2 Hours of {GL}, 36%"


def test_idle_but_media_playing_is_not_idle(client):
    # A movie with zero keyboard/mouse input: the agent sends idle=False, media=True -> counts as active.
    post(client, span("21:00", 60, "IINA", "Interstellar.mkv", step=15, media=True))
    ws = [w for w in _windows(client) if w["window_start"] >= local_ts("21:00")]
    assert len(ws) == 4
    assert {w["category"] for w in ws} == {MOVIES}  # 1 h: below the binge threshold
    assert all(w["features"]["media_share"] == 1.0 for w in ws)


def test_truly_idle_is_dropped(client):
    post(client, span("22:00", 60, "IINA", "Interstellar.mkv", step=15, idle=True))
    assert [w for w in _windows(client) if w["window_start"] >= local_ts("22:00")] == []


def test_youtube_split_by_title_not_domain():
    cfg = load_config()
    eng = RuleEngine(cfg.rules, cfg.signals)
    yt = ("Google Chrome", "youtube.com", "https://www.youtube.com/watch?v=abc123xyz")
    edu = eng.match(*yt, "Stanford CS229: Machine Learning Lecture 1 - YouTube")
    fun = eng.match(*yt, "Funniest cat fails 2026 - YouTube")
    pod = eng.match(*yt, "Lex Fridman Podcast #400 - YouTube")
    assert (edu.signal, fun.signal, pod.signal) == ("video_learning", "entertainment", "video_learning")
    assert fun.confidence < 0.6  # unclear YouTube videos are flagged for the knowledge graph (phase 2)


def test_multi_device_streams_merge(client):
    # Mac idle (nothing sent) while phone is on LinkedIn -> the window is still job applications.
    post(client, span("18:00", 15, "com.linkedin.android", None, device="pixel", source="android", step=30))
    ws = [w for w in _windows(client) if w["window_start"] >= local_ts("18:00")]
    assert ws and ws[0]["category"] == "Career › LinkedIn"
