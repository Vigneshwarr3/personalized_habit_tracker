"""Phase 4: learned model. Inactive below 300 labels; time-based CV vs routines; used only where it wins."""
from __future__ import annotations

from datetime import date, timedelta

from server.app.classify.model import time_folds
from server.app.db import connect
from server.app.ingest import ingest

from .conftest import cat_id, local_ts, span

OFFICE, FRIENDS, LEARN, MOVIES, EXERCISE = ("Work › Office work", "Friends/Family › Spending time",
                                           "Learning › General learning", "Watching › Movies & series", "Exercise")


def _day(cfg, d: str):
    """A synthetic day: morning learning, afternoon Zoom class, evening Zoom with friends, then a movie."""
    hb = []
    for i, (app, title) in enumerate([("GoodNotes", "Notes"), ("Code", "hw.py")] * 4):
        hb += span(f"09:{i * 15 % 60:02d}" if i < 4 else f"10:{(i - 4) * 15:02d}", 15, app, title, step=60, day=d)
    hb += span("14:00", 60, "zoom.us", "Zoom Meeting", step=60, day=d)
    hb += span("20:00", 60, "zoom.us", "Zoom Meeting", step=60, day=d)
    hb += span("21:00", 60, "IINA", "movie.mkv", step=60, media=True, day=d)
    conn = connect(cfg.db_path)
    ingest(conn, cfg, hb, device="mac-test")
    conn.close()


def _label_day(client, d: str):
    # you correct the Zoom hours (routines can't tell work calls from friends), then confirm the rest of the day
    client.post("/labels", json={"start_ts": local_ts("14:00", d), "end_ts": local_ts("15:00", d), "category": OFFICE})
    client.post("/labels", json={"start_ts": local_ts("20:00", d), "end_ts": local_ts("21:00", d), "category": FRIENDS})
    return client.post("/labels/confirm-day", json={"date": d}).json()


def _windows(client, d):
    return {w["window_start"]: w for w in client.get("/windows", params={"start": d, "end": d}).json()["windows"]}


def test_time_folds_never_train_on_the_future():
    folds = time_folds(100, 4)
    assert len(folds) == 4
    for tr, te in folds:
        assert max(tr) < min(te)
    assert time_folds(3, 4) == []


def test_confirm_day_creates_confirmed_labels_once(client, cfg):
    d = "2026-10-01"
    _day(cfg, d)
    res = _label_day(client, d)
    assert res["confirmed_windows"] == 12          # 8 learning + 4 movie windows (Zoom already labeled)
    assert res["labels_created"] == 9               # VS Code blocks (Learning › Coding) alternate with GoodNotes
                                                    # blocks (General learning); the movie hour is one label
    assert client.post("/labels/confirm-day", json={"date": d}).json()["confirmed_windows"] == 0
    st = client.get("/labels/stats").json()
    assert st["by_source"] == {"correction": 8, "confirmed": 12} and st["needed_for_model"] == 300


def test_model_stays_off_below_300_labels(client, cfg):
    for i in range(3):
        d = (date(2026, 10, 1) + timedelta(days=i)).isoformat()
        _day(cfg, d)
        _label_day(client, d)
    meta = client.post("/model/train").json()
    assert meta["active"] is False and meta["n_labeled"] == 60 and "needs 300" in meta["note"]
    assert client.get("/model/status").json()["serving"] is False


def test_model_learns_what_routines_cannot_and_only_there(client, cfg):
    days = [(date(2026, 10, 1) + timedelta(days=i)).isoformat() for i in range(17)]  # 17 x 20 = 340 windows
    for d in days:
        _day(cfg, d)
        _label_day(client, d)
    meta = client.post("/model/train").json()
    assert meta["n_labeled"] == 340 and meta["active"] is True
    CLASS, PARTY, LEARN_ID, MOVIES_ID = (cat_id(client, OFFICE), cat_id(client, FRIENDS), cat_id(client, LEARN),
                                         cat_id(client, MOVIES))
    # routines never get the Zoom hours right (they are Unclassified) -> the model takes over there...
    assert set(meta["enabled_categories"]) == {CLASS, PARTY}
    # ...but not for categories the routines already handle perfectly
    assert meta["metrics"][str(LEARN_ID)]["enabled"] is False and meta["metrics"][str(MOVIES_ID)]["enabled"] is False
    assert meta["metrics"][str(CLASS)]["routines"]["f1"] == 0.0 and meta["metrics"][str(CLASS)]["model"]["f1"] >= 0.9
    assert meta["accuracy"]["combined"] > meta["accuracy"]["routines"]

    # a new, unlabeled day
    new = "2026-10-20"
    _day(cfg, new)
    ws = _windows(client, new)
    assert (ws[local_ts("14:00", new)]["category_id"], ws[local_ts("14:00", new)]["source"]) == (CLASS, "model")
    assert (ws[local_ts("20:15", new)]["category_id"], ws[local_ts("20:15", new)]["source"]) == (PARTY, "model")
    assert (ws[local_ts("09:00", new)]["category_id"], ws[local_ts("09:00", new)]["source"]) == (LEARN_ID, "routine")
    assert (ws[local_ts("21:00", new)]["category_id"], ws[local_ts("21:00", new)]["source"]) == (MOVIES_ID, "routine")

    # your labels still win over the model
    client.post("/labels", json={"start_ts": local_ts("14:00", new), "end_ts": local_ts("14:15", new),
                                 "category": EXERCISE})
    w = _windows(client, new)[local_ts("14:00", new)]
    assert (w["category"], w["source"]) == (EXERCISE, "label")
    assert client.get("/model/status").json()["labels_changed_since_training"] is True
