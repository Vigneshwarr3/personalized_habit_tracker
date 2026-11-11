"""Settings: which Overview cards to show, the rules behind them, session thresholds and location.
Saved on the server, so the Mac and the phone look the same. Activities themselves are edited on Categories."""
from __future__ import annotations

import streamlit as st

from common import api_get, api_send, setup_page

setup_page("Settings")
st.title("Settings")

ui = api_get("/ui-settings")
cats = api_get("/categories")
acts = sorted([c for c in cats if c["is_activity"] and not c["merged_into"] and not c["archived"]],
              key=lambda c: (c["sort_order"], c["id"]))
act_ids = [a["id"] for a in acts]
name_of = {c["id"]: c["name"] for c in cats}
labelable = [c["id"] for c in api_get("/categories/labelable")]
fmt = lambda cid: name_of.get(cid, f"#{cid}")


def idx(options: list, value) -> int:
    return options.index(value) if value in options else 0


with st.form("settings"):
    st.subheader("Overview cards")
    st.caption("Show or hide each card on the Overview page.")
    with st.container(horizontal=True, wrap=True, gap="medium"):
        cards = {k: st.toggle(label, value=ui["cards"].get(k, True), key=f"card-{k}")
                 for k, label in ui["card_labels"].items()}

    st.subheader("Card rules")
    ttc = st.selectbox("Time to cut = this activity", act_ids, index=idx(act_ids, ui["time_to_cut_activity"]),
                       format_func=fmt)
    c1, c2 = st.columns(2)
    s_act = c1.selectbox("Streak: activity", act_ids, index=idx(act_ids, ui["streak"]["activity"]), format_func=fmt)
    s_hours = c2.number_input("Streak: at least (hours per day)", 0.0, 24.0, float(ui["streak"]["min_hours"]), 0.5)
    f_acts = st.multiselect("Focus counts these activities", act_ids,
                            default=[a for a in ui["focus"]["activities"] if a in act_ids], format_func=fmt)
    f_cats = st.multiselect("…and these sub-activities", labelable,
                            default=[c for c in ui["focus"]["categories"] if c in labelable], format_func=fmt)

    st.subheader("Sessions")
    st.caption("A whole session counts once it passes the threshold. Blocks you labeled yourself are never changed.")
    c1, c2 = st.columns(2)
    binge = c1.number_input("Binge watching: watching for at least (hours)", 0.25, 24.0,
                            float(ui["sessions"]["binge_hours"]), 0.25)
    doom = c2.number_input("Doom scrolling: social media / short videos for at least (minutes)", 5.0, 600.0,
                           float(ui["sessions"]["doom_minutes"]), 5.0)

    st.subheader("Location (phone)")
    loc = ui["location"]
    c1, c2, c3 = st.columns(3)
    travel_km = c1.number_input("Travel = farther from home than (km)", 1.0, 5000.0, float(loc["travel_km"]), 5.0)
    precision = c2.selectbox("Coordinate precision", ["100m", "1km"], index=0 if loc["precision"] == "100m" else 1,
                             format_func={"100m": "about 100 m", "1km": "about 1 km"}.get,
                             help="Applies to new data only: stored coordinates can't be made more precise later.")
    radius = c3.number_input("Place radius (m)", 50.0, 2000.0, float(loc["radius_m"]), 10.0,
                             help="How close counts as being at Home, Work or another place")
    autofill = st.toggle("Fill empty blocks at places you've labeled the same way twice", value=loc["autofill_places"])

    if st.form_submit_button("Save settings", type="primary"):
        body = {
            "cards": cards, "time_to_cut_activity": ttc,
            "streak": {"activity": s_act, "min_hours": s_hours},
            "focus": {"activities": f_acts, "categories": f_cats},
            "sessions": {"binge_hours": binge, "doom_minutes": doom},
            "location": {"travel_km": travel_km, "precision": precision, "radius_m": radius,
                         "autofill_places": autofill},
        }
        try:
            api_send("PUT", "/ui-settings", body)
            st.success("Saved.")
            st.rerun()
        except RuntimeError as e:
            st.error(str(e))
st.caption("Activities, sub-activities and their colors are edited on the Categories page.")
