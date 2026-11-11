"""Privacy & devices: pause tracking, hide date ranges (data is kept), device tokens, storage stats."""
from __future__ import annotations

import os
from datetime import time as dtime

import streamlit as st

from common import api_get, api_send, fmt_dt, in_app, local_ts, setup_page, today

setup_page("Privacy & devices")
st.title("Privacy & devices")

# ---- pause ---------------------------------------------------------------------------------------------
st.subheader("Pause tracking")
paused = api_get("/status")["paused"]
st.write("Collectors check this on every upload (about once a minute) and stop recording while paused. "
         "On the Mac, `python -m agent_mac pause` stops it instantly, even offline.")
if st.toggle("Tracking paused", value=paused) != paused:
    api_send("POST", "/pause", {"paused": not paused})
    st.rerun()

# ---- hidden ranges -------------------------------------------------------------------------------------
st.subheader("Hide a date range")
st.write("Hidden time disappears from the dashboard and totals. **Nothing is deleted**; you can un-hide it any time.")
with st.form("hide"):
    c1, c2, c3, c4 = st.columns(4)
    d0 = c1.date_input("From day", today())
    t0 = c2.time_input("From time", dtime(0, 0), step=900)
    d1 = c3.date_input("To day", today())
    t1 = c4.time_input("To time", dtime(23, 45), step=900)
    reason = st.text_input("Reason (optional)")
    if st.form_submit_button("Hide range"):
        a, b = local_ts(d0, t0), local_ts(d1, t1)
        if b <= a:
            st.error("End must be after start.")
        else:
            api_send("POST", "/hidden-ranges", {"start_ts": a, "end_ts": b, "reason": reason or None})
            st.rerun()
for h in api_get("/hidden-ranges"):
    if h["unhidden_at"]:
        continue
    a, b = st.columns([5, 1])
    a.write(f"{fmt_dt(h['start_ts'])} → {fmt_dt(h['end_ts'])}"
            + (f" · {h['reason']}" if h["reason"] else ""))
    if b.button("Un-hide", key=f"uh-{h['id']}"):
        api_send("POST", f"/hidden-ranges/{h['id']}/unhide")
        st.rerun()

# ---- devices -------------------------------------------------------------------------------------------
st.subheader("Devices")
if in_app():
    # inside the app a download can't be installed from the page; the app updates itself instead
    st.link_button("Update app", "habittracker://settings?update=1", icon=":material/system_update:",
                   help="Opens Settings & sync and checks your server for a newer version")
else:
    _apk = os.environ.get("HT_APK_PATH", "/app/dist/habit-tracker.apk")
    if os.path.exists(_apk):
        with open(_apk, "rb") as fh:
            st.download_button("Download the Android app (.apk)", fh.read(), file_name="habit-tracker.apk",
                               mime="application/vnd.android.package-archive",
                               help="On the phone: open this page in Chrome, download, then allow installing "
                                    "unknown apps for Chrome.")
devs = api_get("/devices")
for d in devs:
    seen = fmt_dt(d["last_seen"], "%b %d %H:%M")
    a, b = st.columns([5, 1])
    a.write(f"**{d['name']}** ({d['source']}) · last upload {seen}" + (" · REVOKED" if d["revoked_at"] else ""))
    if not d["revoked_at"] and b.button("Revoke", key=f"rv-{d['name']}"):
        api_send("POST", f"/devices/{d['name']}/revoke")
        st.rerun()
with st.form("newdev"):
    c1, c2 = st.columns(2)
    name = c1.text_input("New device name", placeholder="my-phone")
    source = c2.selectbox("Type", ["android", "mac", "ios"])
    if st.form_submit_button("Create token"):
        try:
            res = api_send("POST", "/devices", {"name": name, "source": source})
            st.success("Copy this token into the device now. It is shown only once.")
            st.code(res["token"])
        except RuntimeError as e:
            st.error(str(e))

# ---- storage ---------------------------------------------------------------------------------------------
st.subheader("Storage")
s = api_get("/stats")
st.write(f"Database size: **{s['db_bytes'] / 1e6:.1f} MB**. Data is never deleted; backups run nightly and are kept.")
for d in s["devices"]:
    st.write(f"- {d['device']} ({d['source']}): {d['samples']:,} samples in {d['spans']:,} spans, "
             f"{fmt_dt(d['first_ts'], '%Y-%m-%d')} → {fmt_dt(d['last_ts'])}")
if st.button("Back up now"):
    st.write(api_send("POST", "/backup"))
