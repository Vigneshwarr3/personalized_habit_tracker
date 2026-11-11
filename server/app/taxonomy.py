"""One-time move to Activities > Sub-activities (agreed 2026-11-08). Nothing is deleted:
old categories are merged (pointer, history kept) or left as they are until you move them yourself."""
from __future__ import annotations

from . import categories as cat_svc

TAXONOMY = [
    ("Watching", "#F4C242", ["Movies & series", "Random YT videos"]),
    ("Learning", "#A5A5A5", ["Scaler website", "YT videos", "Reading books", "Reading research papers", "Coding",
                             "General learning"]),
    ("Project", "#3D5329", ["Coding", "Craft works"]),
    ("Everyday activities", "#E8D4A8", ["Cooking", "Eating", "Chores", "Commute", "Shopping", "Bank/Hospital/Others"]),
    ("Travel", "#777777", ["Tour (different city)", "Visiting places nearby"]),
    ("Sleep", "#4EA9E8", []),        # its own activity, no sub-activities
    ("Exercise", "#9ACD5D", []),     # its own activity, no sub-activities
    ("Unproductive", "#E8352A", ["Social media", "Doom scrolling", "Binge watching", "Negative habits"]),
    ("Work", "#3E444C", ["Office work"]),
    ("Career", "#6E98D2", ["LinkedIn", "Company careers pages", "Editing resume", "Events & networking"]),
    ("Friends/Family", "#F28C6B", ["Talking", "Spending time"]),
]
S = cat_svc.SEP
MERGES = {  # old category -> new sub-activity
    "General Learning": f"Learning{S}General learning",
    "Class": f"Learning{S}General learning",
    "Assignments": f"Learning{S}General learning",
    "Learning for Assignments": f"Learning{S}General learning",
    "Scaler": f"Learning{S}Scaler website",
    "Internships/Linkedin": f"Career{S}LinkedIn",
    "Commute": f"Everyday activities{S}Commute",
    "Office/Work": f"Work{S}Office work",
    "Negative Habits": f"Unproductive{S}Negative habits",
    "Negative habits 2": f"Unproductive{S}Negative habits",
    "Bank/University/Hospital/Others": f"Everyday activities{S}Bank/Hospital/Others",
}
ARCHIVE = ["Class", "Assignments", "Learning for Assignments"]
# left for you to move with "Move old category into…": Movies/YT/Talking with friends, Cooking, Eating & Chores,
# Daily activities, Travel/Visiting places, Being Unproductive, Party/Event/Outing/Networking


def add_columns(conn) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(categories)")}
    for col, ddl in (("parent_id", "INTEGER REFERENCES categories(id)"), ("is_activity", "INTEGER NOT NULL DEFAULT 0"),
                     ("color_custom", "INTEGER NOT NULL DEFAULT 0")):
        if col not in cols:
            conn.execute(f"ALTER TABLE categories ADD COLUMN {col} {ddl}")


def migrate(conn) -> None:
    if conn.execute("SELECT 1 FROM meta WHERE key='taxonomy_v1'").fetchone():
        return
    order = 0
    for act_name, color, subs in TAXONOMY:
        existing = cat_svc.find_by_name(conn, act_name)
        if existing is not None:   # Sleep / Exercise already exist: they become activities themselves
            conn.execute("UPDATE categories SET is_activity=1, color=?, archived=0 WHERE id=?", (color, existing))
            act_id = existing
        else:
            act_id = cat_svc.add_activity(conn, act_name, color).id
        conn.execute("UPDATE categories SET sort_order=? WHERE id=?", (order, act_id))
        order += 1
        for sub in subs:
            if cat_svc.find_by_name(conn, f"{act_name}{S}{sub}") is None:
                c = cat_svc.add_sub(conn, act_id, sub)
                conn.execute("UPDATE categories SET sort_order=? WHERE id=?", (order, c.id))
            order += 1
        cat_svc.reshade(conn, act_id)
    for old, new in MERGES.items():
        src, tgt = cat_svc.find_by_name(conn, old), cat_svc.find_by_name(conn, new)
        if src is None or tgt is None or src == tgt:
            continue
        if cat_svc.get(conn, src).merged_into is None:
            cat_svc.merge(conn, src, tgt)
    for old in ARCHIVE:
        cid = cat_svc.find_by_name(conn, old)
        if cid is not None:
            cat_svc.set_archived(conn, cid, True)
    # old categories you'll move later go after the activities
    for c in cat_svc.all_categories(conn):
        if not c.is_activity and c.parent_id is None and c.merged_into is None:
            conn.execute("UPDATE categories SET sort_order=? WHERE id=?", (1000 + c.id, c.id))
    conn.execute("INSERT INTO meta(key, value) VALUES('taxonomy_v1', '1')")
