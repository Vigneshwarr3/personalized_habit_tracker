"""Category service: add / rename / recolor / archive / merge / unmerge, plus name & merge resolution.

Merges are pointers (merged_into); nothing is rewritten, so history is preserved and resolved at read time.
Renames record the old name as an alias, so routines/rules that use the old name keep working.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass

_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
MAX_CHAIN = 64


class CategoryError(ValueError):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


SEP = " › "  # sub-activity names are stored as "Activity › Sub", so "Coding" can exist under two activities


@dataclass
class Category:
    id: int
    name: str
    color: str
    archived: bool
    merged_into: int | None
    sort_order: int
    parent_id: int | None = None      # the activity this sub-activity belongs to
    is_activity: bool = False
    color_custom: bool = False        # you picked this sub-activity's color (otherwise it follows its activity)

    @property
    def short_name(self) -> str:
        return self.name.split(SEP, 1)[1] if SEP in self.name else self.name

    def as_dict(self) -> dict:
        return self.__dict__.copy() | {"short_name": self.short_name}


def _row(r: sqlite3.Row) -> Category:
    keys = r.keys()
    return Category(r["id"], r["name"], r["color"], bool(r["archived"]), r["merged_into"], r["sort_order"],
                    r["parent_id"] if "parent_id" in keys else None,
                    bool(r["is_activity"]) if "is_activity" in keys else False,
                    bool(r["color_custom"]) if "color_custom" in keys else False)


def shade(hex_color: str, i: int, n: int) -> str:
    """i-th of n shades of an activity color (lighter and darker variants of the same hue)."""
    import colorsys
    if n <= 1:
        return hex_color.upper()
    r, g, b = (int(hex_color[k:k + 2], 16) / 255 for k in (1, 3, 5))
    h, l, s_ = colorsys.rgb_to_hls(r, g, b)
    lo_lim, hi_lim, spread = 0.18, 0.86, min(0.40, 0.08 * (n - 1) + 0.10)
    lo, hi = l - spread / 2, l + spread / 2
    if hi > hi_lim:   # shift the whole range instead of clipping, so shades never collapse together
        lo, hi = lo - (hi - hi_lim), hi_lim
    if lo < lo_lim:
        lo, hi = lo_lim, min(hi_lim, hi + (lo_lim - lo))
    nl = lo + (hi - lo) * i / (n - 1)
    r, g, b = colorsys.hls_to_rgb(h, nl, s_)
    return "#{:02X}{:02X}{:02X}".format(round(r * 255), round(g * 255), round(b * 255))


def children(conn: sqlite3.Connection, activity_id: int, include_hidden: bool = False) -> list[Category]:
    rows = conn.execute("SELECT * FROM categories WHERE parent_id=? ORDER BY sort_order, id", (activity_id,)).fetchall()
    cats = [_row(r) for r in rows]
    return cats if include_hidden else [c for c in cats if not c.archived and c.merged_into is None]


def reshade(conn: sqlite3.Connection, activity_id: int) -> None:
    act = get(conn, activity_id)
    subs = children(conn, activity_id)
    for i, c in enumerate(subs):
        if not c.color_custom:
            conn.execute("UPDATE categories SET color=?, updated_at=? WHERE id=?",
                         (shade(act.color, i, len(subs)), time.time(), c.id))


def add_activity(conn: sqlite3.Connection, name: str, color: str) -> Category:
    if SEP.strip() in name:
        raise CategoryError("activity names can't contain ›")
    c = add(conn, name, color)
    conn.execute("UPDATE categories SET is_activity=1 WHERE id=?", (c.id,))
    _event(conn, c.id, "make_activity", {})
    return get(conn, c.id)


def add_sub(conn: sqlite3.Connection, activity_id: int, sub_name: str, color: str | None = None) -> Category:
    act = get(conn, activity_id)
    if not act.is_activity:
        raise CategoryError(f"category {activity_id} is not an activity")
    sub_name = sub_name.strip()
    if not sub_name or SEP.strip() in sub_name:
        raise CategoryError("sub-activity name must be non-empty and can't contain ›")
    c = add(conn, f"{act.name}{SEP}{sub_name}", color or act.color)
    conn.execute("UPDATE categories SET parent_id=?, color_custom=? WHERE id=?", (activity_id, int(bool(color)), c.id))
    _event(conn, c.id, "add_sub", {"activity": activity_id})
    reshade(conn, activity_id)
    return get(conn, c.id)


def labelable(conn: sqlite3.Connection) -> list[Category]:
    """What you can pick in corrections: sub-activities, activities without sub-activities, and old categories
    you haven't moved yet."""
    out = []
    for c in all_categories(conn, include_hidden=False):
        if c.is_activity and children(conn, c.id):
            continue
        out.append(c)
    return out


def activity_of(cats: dict[int, "Category"], cid: int | None) -> "Category | None":
    """The activity a category belongs to (an activity is its own; an old unsorted category has none)."""
    c = cats.get(cid) if cid is not None else None
    if c is None:
        return None
    if c.parent_id is not None:
        return cats.get(c.parent_id)
    return c if c.is_activity else None


def _event(conn: sqlite3.Connection, cid: int, action: str, detail: dict) -> None:
    conn.execute(
        "INSERT INTO category_events(ts, category_id, action, detail) VALUES(?,?,?,?)",
        (time.time(), cid, action, json.dumps(detail)),
    )


def get(conn: sqlite3.Connection, cid: int) -> Category:
    r = conn.execute("SELECT * FROM categories WHERE id=?", (cid,)).fetchone()
    if r is None:
        raise CategoryError(f"category {cid} not found", 404)
    return _row(r)


def all_categories(conn: sqlite3.Connection, include_hidden: bool = True) -> list[Category]:
    rows = conn.execute("SELECT * FROM categories ORDER BY sort_order, id").fetchall()
    cats = [_row(r) for r in rows]
    if not include_hidden:
        cats = [c for c in cats if not c.archived and c.merged_into is None]
    return cats


def resolve_id(conn: sqlite3.Connection, cid: int | None) -> int | None:
    """Follow merged_into pointers to the final category. Cycle-guarded."""
    if cid is None:
        return None
    seen: set[int] = set()
    cur = cid
    for _ in range(MAX_CHAIN):
        if cur in seen:
            raise CategoryError(f"merge cycle detected at category {cur}", 500)
        seen.add(cur)
        r = conn.execute("SELECT merged_into FROM categories WHERE id=?", (cur,)).fetchone()
        if r is None:
            return None
        if r["merged_into"] is None:
            return cur
        cur = r["merged_into"]
    raise CategoryError("merge chain too long", 500)


def find_by_name(conn: sqlite3.Connection, name: str) -> int | None:
    """Exact (case-insensitive) current name, else a former name (alias). Not merge-resolved."""
    r = conn.execute("SELECT id FROM categories WHERE name=?", (name.strip(),)).fetchone()
    if r:
        return r["id"]
    r = conn.execute("SELECT category_id FROM category_aliases WHERE alias=?", (name.strip(),)).fetchone()
    return r["category_id"] if r else None


def resolve_name(conn: sqlite3.Connection, name: str) -> int | None:
    return resolve_id(conn, find_by_name(conn, name))


def resolve_ref(conn: sqlite3.Connection, ref: int | str) -> int:
    """Accept an id or a name; return the final (merge-resolved) id."""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        cid = resolve_id(conn, int(ref))
    else:
        cid = resolve_name(conn, str(ref))
    if cid is None:
        raise CategoryError(f"unknown category {ref!r}", 404)
    return cid


def _check_name_free(conn: sqlite3.Connection, name: str, own_id: int | None = None) -> None:
    name = name.strip()
    if not name or len(name) > 80:
        raise CategoryError("name must be 1-80 characters")
    r = conn.execute("SELECT id FROM categories WHERE name=?", (name,)).fetchone()
    if r and r["id"] != own_id:
        raise CategoryError(f"a category named {name!r} already exists", 409)
    a = conn.execute("SELECT category_id FROM category_aliases WHERE alias=?", (name,)).fetchone()
    if a and a["category_id"] != own_id:
        raise CategoryError(f"{name!r} is a former name of category {a['category_id']}", 409)


def _check_color(color: str) -> str:
    if not _COLOR.match(color or ""):
        raise CategoryError("color must look like #RRGGBB")
    return color.upper()


def add(conn: sqlite3.Connection, name: str, color: str) -> Category:
    _check_name_free(conn, name)
    color = _check_color(color)
    now = time.time()
    order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM categories").fetchone()[0]
    cur = conn.execute(
        "INSERT INTO categories(name, color, sort_order, created_at, updated_at) VALUES(?,?,?,?,?)",
        (name.strip(), color, order, now, now),
    )
    cid = cur.lastrowid
    _event(conn, cid, "add", {"name": name.strip(), "color": color})
    return get(conn, cid)


def rename(conn: sqlite3.Connection, cid: int, new_name: str) -> Category:
    cat = get(conn, cid)
    new_name = new_name.strip()
    if cat.parent_id is not None and SEP not in new_name:  # renaming a sub-activity by its short name
        new_name = f"{get(conn, cat.parent_id).name}{SEP}{new_name}"
    if cat.is_activity and new_name != cat.name:
        renamed = _rename_one(conn, cat, new_name)
        for ch in children(conn, cid, include_hidden=True):  # "Old › Sub" -> "New › Sub" (old names kept as aliases)
            _rename_one(conn, ch, f"{new_name}{SEP}{ch.short_name}")
        return renamed
    return _rename_one(conn, cat, new_name)


def _rename_one(conn: sqlite3.Connection, cat: Category, new_name: str) -> Category:
    cid = cat.id
    if new_name == cat.name:
        return cat
    _check_name_free(conn, new_name, own_id=cid)
    now = time.time()
    conn.execute("BEGIN")
    try:
        # (renaming back to one of our own old names is fine: name lookup is checked before aliases)
        conn.execute(
            "INSERT OR REPLACE INTO category_aliases(alias, category_id, created_at) VALUES(?,?,?)",
            (cat.name, cid, now),
        )
        conn.execute("UPDATE categories SET name=?, updated_at=? WHERE id=?", (new_name, now, cid))
        _event(conn, cid, "rename", {"from": cat.name, "to": new_name})
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return get(conn, cid)


def recolor(conn: sqlite3.Connection, cid: int, color: str) -> Category:
    cat = get(conn, cid)
    color = _check_color(color)
    custom = 1 if cat.parent_id is not None else int(cat.color_custom)
    conn.execute("UPDATE categories SET color=?, color_custom=?, updated_at=? WHERE id=?", (color, custom, time.time(), cid))
    _event(conn, cid, "recolor", {"from": cat.color, "to": color})
    if cat.is_activity:
        reshade(conn, cid)  # sub-activities you haven't recolored follow the new activity color
    return get(conn, cid)


def follow_activity_color(conn: sqlite3.Connection, cid: int) -> Category:
    """Undo a custom sub-activity color: it goes back to a shade of its activity."""
    cat = get(conn, cid)
    conn.execute("UPDATE categories SET color_custom=0 WHERE id=?", (cid,))
    if cat.parent_id is not None:
        reshade(conn, cat.parent_id)
    return get(conn, cid)


def set_archived(conn: sqlite3.Connection, cid: int, archived: bool) -> Category:
    get(conn, cid)
    conn.execute("UPDATE categories SET archived=?, updated_at=? WHERE id=?", (int(archived), time.time(), cid))
    _event(conn, cid, "archive" if archived else "unarchive", {})
    return get(conn, cid)


def merge(conn: sqlite3.Connection, source_id: int, target_id: int) -> Category:
    """Merge source INTO target. Everything labeled/classified as source is shown as target."""
    if source_id == target_id:
        raise CategoryError("cannot merge a category into itself")
    src = get(conn, source_id)
    get(conn, target_id)
    if src.merged_into is not None:
        raise CategoryError(f"category {source_id} is already merged into {src.merged_into}", 409)
    final_target = resolve_id(conn, target_id)
    if final_target == source_id:
        raise CategoryError("merge would create a cycle", 409)
    conn.execute("UPDATE categories SET merged_into=?, updated_at=? WHERE id=?", (target_id, time.time(), source_id))
    _event(conn, source_id, "merge", {"into": target_id})
    return get(conn, source_id)


def unmerge(conn: sqlite3.Connection, cid: int) -> Category:
    cat = get(conn, cid)
    if cat.merged_into is None:
        return cat
    conn.execute("UPDATE categories SET merged_into=NULL, updated_at=? WHERE id=?", (time.time(), cid))
    _event(conn, cid, "unmerge", {"was_into": cat.merged_into})
    return get(conn, cid)


def history(conn: sqlite3.Connection, cid: int | None = None) -> list[dict]:
    q = "SELECT * FROM category_events"
    args: tuple = ()
    if cid is not None:
        q += " WHERE category_id=?"
        args = (cid,)
    rows = conn.execute(q + " ORDER BY id", args).fetchall()
    return [dict(r) | {"detail": json.loads(r["detail"] or "{}")} for r in rows]
