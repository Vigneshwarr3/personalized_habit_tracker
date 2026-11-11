"""Corrections: review a day's 15-minute windows and fix categories. Every correction is stored as a label
(the highest-priority stage of the classifier, and training data for the learned model later)."""
from __future__ import annotations

from datetime import time as dtime, timedelta

import pandas as pd
import streamlit as st

from common import api_get, api_send, fmt_dt, fmt_time, local_ts, setup_page, today

setup_page("Corrections")
st.title("Corrections")

cats = api_get("/categories/labelable")  # sub-activities (+ old categories you haven't moved yet)

day = st.date_input("Day", today())
data = api_get("/windows", start=day.isoformat(), end=day.isoformat())
windows = data["windows"]

stats = api_get("/labels/stats")
need = stats["needed_for_model"]
have = stats["labeled_windows_approx"]
c_prog, c_btn = st.columns([3, 2])
c_prog.progress(min(1.0, have / need), text=f"{have} / {need} labeled 15-min blocks for the learned model"
                                            + (" ✓" if have >= need else ""))
todo = sum(1 for w in windows if w["category_id"] is not None and w["source"] != "label")
if c_btn.button(f"Confirm this day ({todo} blocks)", disabled=todo == 0,
                help="Fix wrong rows first. Then this saves every remaining classified block of this day as "
                     "correct - that's what the learned model trains on."):
    res = api_send("POST", "/labels/confirm-day", {"date": day.isoformat()})
    st.success(f"Confirmed {res['confirmed_windows']} blocks.")
    st.rerun()

# ---- the whole day, 96 blocks (empty ones included) -----------------------------------------------
by_start = {round(w["window_start"]): w for w in windows}
locs = data.get("locations", {})
color_of = {c["name"]: c["color"] for c in api_get("/categories")}
day_a, day_b = local_ts(day, dtime(0)), local_ts(day + timedelta(days=1), dtime(0))
rows = []
t = day_a
while t < day_b:
    w = by_start.get(round(t))
    f = (w or {}).get("features") or {}
    what = "; ".join(f"{(i.get('app_name') or i['app']) if not (i['title'] or i['domain']) else (i['title'] or i['domain'])[:60]}"
                     f" [{i['signal']}] {i['minutes']}m" for i in f.get("top_items", [])[:3])
    if w is None:
        how, cat = "empty", ""
    else:
        cat = w["category"] or "Unclassified"
        how = {"label": "your label", "routine": f"routine: {w['routine']}", "location": "location",
               "session": w.get("routine") or "session",
               "model": f"learned model ({w['confidence']:.0%})" if w["confidence"] else "learned model",
               "unclassified": "no routine matched"}.get(w["source"], w["source"])
    loc = locs.get(str(round(t))) or {}
    if how == "location":
        how = "your location"
    rows.append({"start_ts": t, "end_ts": t + 900, " ": "", "Time": f"{fmt_time(t)}–{fmt_time(t + 900)}",
                 "Category": cat, "Location": loc.get("label", ""), "How": how,
                 "Devices": ", ".join(f.get("devices", [])),
                 "What": what or ("" if w is None else "(labeled by you)")})
    t += 900
df = pd.DataFrame(rows)


def apply_spans(blocks: list[tuple[float, float]], cat_id: int, note: str | None = None) -> int:
    """Back-to-back blocks become one label."""
    spans: list[list[float]] = []
    for a, b in sorted(blocks):
        if spans and abs(spans[-1][1] - a) < 1:
            spans[-1][1] = b
        else:
            spans.append([a, b])
    for a, b in spans:
        api_send("POST", "/labels", {"start_ts": a, "end_ts": b, "category": cat_id, "note": note})
    return len(spans)


tab_table, tab_fill, tab_labels = st.tabs(["Day table", "Quick fill", "Labels this day"])

with tab_table:
    styled = df[[" ", "Time", "Category", "Location", "How", "Devices", "What"]].style.apply(
        lambda col: [f"background-color: {color_of.get(c, 'transparent')}" for c in df["Category"]], subset=[" "])
    sel = st.dataframe(styled, hide_index=True, width="stretch", height=560, key=f"day-{day}",
                       on_select="rerun", selection_mode="multi-row",
                       column_config={" ": st.column_config.TextColumn(" ", width=12),
                                      "What": st.column_config.TextColumn("What (top items)", width="large")})
    picked = sel.selection.rows if sel else []
    with st.container(border=True):
        c1, c2, c3 = st.columns([3, 2, 1], vertical_alignment="bottom")
        c1.markdown(f"**{len(picked)} block{'s' if len(picked) != 1 else ''} selected**" if picked else
                    "Tick rows in the table (several at once), then set their category here.")
        new_cat = c2.selectbox("Category", cats, format_func=lambda c: c["name"], key="bulk-cat",
                               label_visibility="collapsed")
        if c3.button("Apply", type="primary", disabled=not picked, width="stretch"):
            n = apply_spans([(df.iloc[i]["start_ts"], df.iloc[i]["end_ts"]) for i in picked], new_cat["id"])
            st.success(f"Saved {len(picked)} blocks as {new_cat['name']} ({n} label{'s' if n > 1 else ''}).")
            st.rerun()

with tab_fill:
    fill_cat = st.selectbox("Category", cats, format_func=lambda c: c["name"], key="fill-cat")
    empty = [(r["start_ts"], r["end_ts"]) for r in rows if r["How"] == "empty"]
    with st.container(horizontal=True, wrap=True, gap="small"):
        if st.button(f"Fill all empty blocks ({len(empty)})", disabled=not empty, type="primary"):
            n = apply_spans(empty, fill_cat["id"], "filled empty blocks")
            st.success(f"Filled {len(empty)} empty blocks as {fill_cat['name']}.")
            st.rerun()
        if st.button("Set 00:00–08:00"):
            apply_spans([(day_a, local_ts(day, dtime(8)))], fill_cat["id"])
            st.success(f"00:00–08:00 set to {fill_cat['name']}.")
            st.rerun()
    st.markdown("**Or a custom range**")
    c1, c2 = st.columns(2)
    t0 = c1.time_input("From", dtime(9, 0), step=900)
    t1 = c2.time_input("To (00:00 = midnight at the end of the day)", dtime(10, 0), step=900)
    note = st.text_input("Note (optional)")
    if st.button("Set range"):
        a = local_ts(day, t0)
        b = local_ts(day + timedelta(days=1), t1) if t1 <= t0 else local_ts(day, t1)
        api_send("POST", "/labels", {"start_ts": a, "end_ts": b, "category": fill_cat["id"], "note": note or None})
        st.success(f"{t0:%H:%M}–{t1:%H:%M} set to {fill_cat['name']}.")
        st.rerun()
    st.caption("These set the category even where something was tracked (your labels always win). "
               "Fill all empty only touches blocks with nothing in them.")

places_today = {}
for v in locs.values():
    if v.get("place_id") and v.get("state") in ("place", "home", "work"):
        places_today[v["place_id"]] = v["label"]
if places_today:
    all_places = {p["id"]: p for p in api_get("/places")}
    tag_options = [None] + [c["id"] for c in cats]
    tag_name = {c["id"]: c["name"] for c in cats}
    with st.expander(f"Places this day ({len(places_today)})"):
        st.caption("Name a place and pick what you do there. Places with the same name share the tag "
                   "(tag one Kroger and every Kroger counts as Shopping). Not sure where it was? Check it on "
                   "OpenStreetMap.")
        for pid in places_today:
            pl = all_places.get(pid)
            if pl is None:
                continue
            with st.container(border=True):
                c1, c2 = st.columns(2)
                name = c1.text_input("Name", pl["name"] or "", placeholder=pl["label"], key=f"pn-{pid}")
                tag = c2.selectbox("Time here counts as", tag_options,
                                   index=tag_options.index(pl.get("category_id")) if pl.get("category_id") in tag_options else 0,
                                   format_func=lambda t: "— not tagged —" if t is None else tag_name.get(t, f"#{t}"),
                                   key=f"pt-{pid}")
                sug = pl.get("osm_suggestion")
                if sug:
                    st.caption("OpenStreetMap: " + " · ".join(x for x in (sug.get("name"), sug.get("type"),
                                                                           sug.get("address")) if x))
                with st.container(horizontal=True, gap="small"):
                    if st.button("Save", key=f"ps-{pid}", type="primary"):
                        body = {"name": name.strip()} if name.strip() else {}
                        body |= {"category_id": tag} if tag else {"clear_tag": True}
                        api_send("PATCH", f"/places/{pid}", body)
                        st.rerun()
                    st.link_button("Open in OpenStreetMap", pl["osm_url"], icon=":material/map:")
                    if st.button("Suggest a name", key=f"pg-{pid}", icon=":material/search:",
                                 help="Asks OpenStreetMap what is at this spot (sends only its rounded coordinates)"):
                        try:
                            api_send("POST", f"/places/{pid}/suggest")
                            st.rerun()
                        except RuntimeError as e:
                            st.error(str(e))
                    if sug and sug.get("name") and sug["name"] != pl["name"]:
                        if st.button(f"Use “{sug['name']}”", key=f"pu-{pid}"):
                            api_send("PATCH", f"/places/{pid}", {"name": sug["name"]})
                            st.rerun()

with tab_labels:
    labels = api_get("/labels", start=day.isoformat(), end=day.isoformat())
    id_to_name = {c["id"]: c["name"] for c in api_get("/categories")}
    if not labels:
        st.info("No labels on this day.")
    for lb in labels:
        c1, c2 = st.columns([5, 1])
        c1.write(f"{fmt_time(lb['start_ts'])}–{fmt_time(lb['end_ts'])} · **{id_to_name.get(lb['category_id'], '?')}**"
                 + (f" · {lb['note']}" if lb.get("note") else "")
                 + f" · added {fmt_dt(lb['created_at'], '%b %d %H:%M')}")
        if c2.button("Undo", key=f"undo-{lb['id']}"):
            api_send("POST", f"/labels/{lb['id']}/undo")
            st.rerun()
    st.caption("Undo keeps the label in history (it is deactivated, not deleted).")
