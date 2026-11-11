"""Activities and sub-activities: colors (sub-activities follow their activity's color unless you pick one),
renames, new ones, archive, and moving old categories into a sub-activity. Nothing is deleted."""
from __future__ import annotations

import html

import streamlit as st

from common import api_get, api_send, is_mobile, setup_page

setup_page("Categories")
st.title("Activities")

cats = api_get("/categories")
by_id = {c["id"]: c for c in cats}
live = [c for c in cats if not c["merged_into"]]
activities = sorted([c for c in live if c["is_activity"]], key=lambda c: (c["sort_order"], c["id"]))
subs_of = {a["id"]: sorted([c for c in live if c["parent_id"] == a["id"]], key=lambda c: (c["sort_order"], c["id"]))
           for a in activities}
old = [c for c in live if not c["is_activity"] and c["parent_id"] is None and not c["archived"]]


def run(fn, ok="Saved."):
    try:
        fn()
        st.toast(ok)
        st.rerun()
    except RuntimeError as e:
        st.error(str(e))


def swatch(color: str, size: int = 14) -> str:
    return (f"<span style='display:inline-block;width:{size}px;height:{size}px;border-radius:4px;background:{color};"
            f"box-shadow:inset 0 0 0 1px rgba(128,128,128,.3);vertical-align:middle'></span>")


# ---- overview: every activity with its sub-activities --------------------------------------------------
with st.container(horizontal=True, wrap=True, gap="small"):
    for a in activities:
        subs = [s for s in subs_of[a["id"]] if not s["archived"]]
        rows = "".join(f"<div style='margin:3px 0'>{swatch(s['color'], 11)} {html.escape(s['short_name'])}</div>"
                       for s in subs) or "<div style='opacity:.6'>no sub-activities</div>"
        with st.container(border=True, width="stretch" if is_mobile() else 250):
            st.html(f"<div style='font-weight:600;margin-bottom:6px'>{swatch(a['color'], 16)} "
                    f"{html.escape(a['name'])}</div>{rows}")

st.divider()
tab_act, tab_sub, tab_new, tab_move = st.tabs(["Edit an activity", "Edit a sub-activity", "New activity",
                                               f"Move old categories ({len(old)})"])

with tab_act:
    a = st.selectbox("Activity", activities, format_func=lambda c: c["name"], key="act-pick")
    if a:
        with st.form(f"act-{a['id']}"):
            name = st.text_input("Name", a["name"])
            color = st.color_picker("Color (sub-activities follow it unless you gave them their own)", a["color"])
            new_sub = st.text_input("Add a sub-activity (optional)", placeholder="e.g. Podcasts")
            if st.form_submit_button("Save", type="primary"):
                def save():
                    patch = {}
                    if name.strip() != a["name"]:
                        patch["name"] = name.strip()
                    if color.upper() != a["color"].upper():
                        patch["color"] = color
                    if patch:
                        api_send("PATCH", f"/categories/{a['id']}", patch)
                    if new_sub.strip():
                        api_send("POST", f"/categories/{a['id']}/subs", {"name": new_sub.strip()})
                run(save)

with tab_sub:
    pairs = [(a, s) for a in activities for s in subs_of[a["id"]]]
    pick = st.selectbox("Sub-activity", pairs, format_func=lambda p: f"{p[0]['name']} › {p[1]['short_name']}"
                        + (" (archived)" if p[1]["archived"] else ""), key="sub-pick")
    if pick:
        a, s = pick
        with st.form(f"sub-{s['id']}"):
            name = st.text_input("Name", s["short_name"])
            color = st.color_picker("Color", s["color"])
            archived = st.checkbox("Archived (hidden from pickers; history kept)", s["archived"])
            if st.form_submit_button("Save", type="primary"):
                def save():
                    patch = {}
                    if name.strip() != s["short_name"]:
                        patch["name"] = name.strip()
                    if color.upper() != s["color"].upper():
                        patch["color"] = color
                    if archived != s["archived"]:
                        patch["archived"] = archived
                    if patch:
                        api_send("PATCH", f"/categories/{s['id']}", patch)
                run(save)
        if s["color_custom"] and st.button("Use a shade of the activity color again"):
            run(lambda: api_send("POST", f"/categories/{s['id']}/follow-activity-color"))

with tab_new:
    with st.form("new-act"):
        name = st.text_input("Activity name")
        color = st.color_picker("Color", "#7F8C99")
        subs = st.text_input("Sub-activities (comma-separated, optional)", placeholder="e.g. Guitar, Singing")
        if st.form_submit_button("Add activity", type="primary") and name.strip():
            def add():
                new = api_send("POST", "/categories/activities", {"name": name.strip(), "color": color})
                for sub in [x.strip() for x in subs.split(",") if x.strip()]:
                    api_send("POST", f"/categories/{new['id']}/subs", {"name": sub})
            run(add, "Activity added.")

with tab_move:
    st.caption("These are your categories from before activities. Move each into a sub-activity when you're ready: "
               "everything recorded under it then shows as that sub-activity. It's reversible (unmerge) and "
               "nothing is rewritten.")
    if not old:
        st.success("All old categories have been moved.")
    targets = [s for a in activities for s in subs_of[a["id"]] if not s["archived"]] + \
              [a for a in activities if not subs_of[a["id"]]]
    for c in old:
        c1, c2, c3 = st.columns([3, 3, 1], vertical_alignment="bottom")
        c1.html(f"{swatch(c['color'])} {html.escape(c['name'])}")
        tgt = c2.selectbox("Move into", targets, format_func=lambda t: t["name"], key=f"mv-{c['id']}",
                           label_visibility="collapsed")
        if c3.button("Move", key=f"mvb-{c['id']}"):
            run(lambda c=c, tgt=tgt: api_send("POST", "/categories/merge", {"source": c["id"], "target": tgt["id"]}),
                f"Moved {c['name']} into {tgt['name']}.")
    moved = [c for c in cats if c["merged_into"] and not c["is_activity"] and c["parent_id"] is None
             and not c["archived"]]
    if moved:
        with st.expander(f"Already moved ({len(moved)})"):
            for c in moved:
                a1, a2 = st.columns([5, 1])
                a1.write(f"{c['name']} → {by_id[c['merged_into']]['name']}")
                if a2.button("Undo", key=f"um-{c['id']}"):
                    run(lambda c=c: api_send("POST", f"/categories/{c['id']}/unmerge"))
