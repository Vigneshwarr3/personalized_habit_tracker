"""Category rename/merge survive; labels override; hidden ranges; nothing is ever deleted."""
from __future__ import annotations

from pathlib import Path

from .conftest import DAY, local_ts, post, span, synthetic_day

from .conftest import BINGE, GL, MOVIES, cat_id


def _cat_at(client, hhmm):
    ws = client.get("/windows", params={"start": DAY, "end": DAY}).json()["windows"]
    t = local_ts(hhmm)
    hit = [w for w in ws if w["window_start"] == t]
    return hit[0] if hit else None


def test_activities_and_sub_activities(client):
    cats = client.get("/categories").json()
    acts = [c for c in cats if c["is_activity"]]
    assert [a["name"] for a in sorted(acts, key=lambda a: a["sort_order"])] == [
        "Watching", "Learning", "Project", "Everyday activities", "Travel", "Sleep", "Exercise", "Unproductive",
        "Work", "Career", "Friends/Family"]
    by = {c["name"]: c for c in cats}
    learning = by["Learning"]
    subs = [c for c in cats if c["parent_id"] == learning["id"] and not c["merged_into"]]
    assert [c["short_name"] for c in sorted(subs, key=lambda c: c["sort_order"])] == [
        "Scaler website", "YT videos", "Reading books", "Reading research papers", "Coding", "General learning"]
    assert len({c["color"] for c in subs}) == 6                           # distinct shades of the activity color
    assert by["Sleep"]["color"] == "#4EA9E8" and by["Exercise"]["color"] == "#9ACD5D"
    # old categories: merged (history kept), archived, or left for you to move
    assert by["General Learning"]["merged_into"] == by[GL]["id"]
    assert by["Class"]["archived"] and by["Class"]["merged_into"] == by[GL]["id"]
    assert by["Internships/Linkedin"]["merged_into"] == by["Career › LinkedIn"]["id"]
    assert by["Negative habits 2"]["merged_into"] == by["Unproductive › Negative habits"]["id"]
    for old in ("Movies/YT/Talking with friends", "Cooking, Eating & Chores", "Daily activities",
                "Travel/Visiting places", "Being Unproductive", "Party/Event/Outing/Networking"):
        assert by[old]["merged_into"] is None and by[old]["parent_id"] is None and not by[old]["is_activity"]
    pick = {c["name"] for c in client.get("/categories/labelable").json()}
    assert GL in pick and "Sleep" in pick and "Learning" not in pick and "Class" not in pick


def test_activity_color_flows_to_sub_activities(client):
    watching = cat_id(client, "Watching")
    movies = cat_id(client, MOVIES)
    client.patch(f"/categories/{movies}", json={"color": "#112233"})                 # your own color sticks...
    client.patch(f"/categories/{watching}", json={"color": "#00AA88"})
    cats = {c["id"]: c for c in client.get("/categories").json()}
    assert cats[movies]["color"] == "#112233" and cats[movies]["color_custom"]
    yt = cats[cat_id(client, "Watching › Random YT videos")]
    assert yt["color"] != "#F8D98A" and not yt["color_custom"]                      # ...others re-shade
    client.post(f"/categories/{movies}/follow-activity-color")
    assert {c["id"]: c for c in client.get("/categories").json()}[movies]["color_custom"] is False


def test_rename_cascades_and_routines_keep_working(client):
    post(client, synthetic_day())
    watching = cat_id(client, "Watching")
    client.patch(f"/categories/{cat_id(client, MOVIES)}", json={"name": "Films & shows"})   # short name
    client.patch(f"/categories/{watching}", json={"name": "Screen time"})                  # activity: cascades
    names = {c["name"] for c in client.get("/categories").json()}
    assert "Screen time › Films & shows" in names and "Screen time › Random YT videos" in names
    post(client, span("21:00", 30, "Google Chrome", "Netflix", "https://www.netflix.com/watch/9", step=15))
    w = _cat_at(client, "21:00")                       # routines.yaml still says "Watching › Movies & series"
    assert (w["category"], w["activity"]) == ("Screen time › Films & shows", "Screen time")
    assert client.post("/categories/activities", json={"name": "Watching", "color": "#000000"}).status_code == 409


def test_move_old_category_into_sub_activity(client):
    post(client, synthetic_day())
    old = cat_id(client, "Being Unproductive")
    lid = client.post("/labels", json={"start_ts": local_ts("20:00"), "end_ts": local_ts("20:30"), "category": old}).json()["id"]
    assert _cat_at(client, "20:00")["category"] == "Being Unproductive"
    assert _cat_at(client, "20:00")["activity"] == "Being Unproductive"          # unsorted: stands for itself
    social = cat_id(client, "Unproductive › Social media")
    assert client.post("/categories/merge", json={"source": old, "target": social}).status_code == 200
    w = _cat_at(client, "20:00")
    assert (w["category"], w["activity"]) == ("Unproductive › Social media", "Unproductive")
    labels = client.get("/labels", params={"start": DAY, "end": DAY}).json()
    assert [lb["category_id"] for lb in labels if lb["id"] == lid] == [old]   # history: label still points at the old one
    assert client.post("/categories/merge", json={"source": social, "target": old}).status_code == 409  # cycle guard
    client.post(f"/categories/{old}/unmerge")
    assert _cat_at(client, "20:00")["category"] == "Being Unproductive"


def test_label_overrides_routine_and_undo(client):
    post(client, synthetic_day())
    assert _cat_at(client, "14:00")["category"] == BINGE
    lid = client.post("/labels", json={"start_ts": local_ts("14:00"), "end_ts": local_ts("15:00"),
                                       "category": cat_id(client, GL)}).json()["id"]
    w = _cat_at(client, "14:00")
    assert (w["category"], w["source"]) == (GL, "label")
    assert _cat_at(client, "15:00")["category"] == MOVIES        # the label split the session: 1 h left < 2 h
    client.post("/labels", json={"start_ts": local_ts("14:00"), "end_ts": local_ts("14:15"), "category": "Exercise"})
    assert _cat_at(client, "14:00")["category"] == "Exercise"
    assert _cat_at(client, "14:15")["category"] == GL
    client.post(f"/labels/{lid}/undo")                              # undo keeps the row (inactive)
    assert _cat_at(client, "14:15")["category"] == MOVIES          # 14:15-16:00 = 1 h 45 < 2 h
    all_labels = client.get("/labels", params={"start": DAY, "end": DAY, "include_inactive": True}).json()
    assert len(all_labels) == 2


def test_label_can_fill_blank_hours(client):
    client.post("/labels", json={"start_ts": local_ts("07:00"), "end_ts": local_ts("08:00"), "category": "Exercise"})
    w = _cat_at(client, "07:00")
    assert w["category"] == "Exercise" and w["features"] is None


def test_hidden_range_hides_but_keeps_data(client):
    post(client, synthetic_day())
    before = client.get("/stats").json()["devices"][0]["spans"]
    hid = client.post("/hidden-ranges", json={"start_ts": local_ts("14:00"), "end_ts": local_ts("16:00"),
                                              "reason": "private"}).json()["id"]
    assert _cat_at(client, "14:00") is None
    d = client.get("/dashboard", params={"start": DAY, "end": DAY}).json()
    assert BINGE not in {r["category"] for r in d["summary"]["categories"]}
    assert client.get("/stats").json()["devices"][0]["spans"] == before
    client.post(f"/hidden-ranges/{hid}/unhide")
    assert _cat_at(client, "14:00")["category"] == BINGE


def test_category_validation(client):
    assert client.post("/categories", json={"name": "Reading", "color": "red"}).status_code == 400
    assert client.post("/categories", json={"name": "class", "color": "#123456"}).status_code == 409
    r = client.post("/categories", json={"name": "Reading", "color": "#123456"})
    assert r.status_code == 201
    assert client.patch(f"/categories/{r.json()['id']}", json={"archived": True}).json()["archived"] is True
    sub = client.post(f"/categories/{cat_id(client, 'Project')}/subs", json={"name": "Woodworking"})
    assert sub.status_code == 201 and sub.json()["name"] == "Project › Woodworking"
    assert client.post(f"/categories/{cat_id(client, GL)}/subs", json={"name": "x"}).status_code == 400  # not an activity


def test_no_delete_statements_on_collected_data():
    """Guard: the server code never issues DELETE/DROP against collected data."""
    root = Path(__file__).resolve().parents[1] / "app"
    offenders = [
        str(p) for p in root.rglob("*.py")
        if any(k in p.read_text().upper() for k in ("DELETE FROM", "DROP TABLE", "TRUNCATE"))
    ]
    assert offenders == []
