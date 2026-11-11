"""Learned model: progress to 300 labels, per-category precision/recall vs the routines, which categories it serves."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import api_get, api_send, fmt_dt, setup_page

setup_page("Model")
st.title("Learned model")
st.caption("A LightGBM model trained on your labeled 15-minute blocks. It is tested on later days than it learned "
           "from, compared with your routines on the same blocks, and used only for categories where it is better. "
           "Your labels always win; routines remain the fallback. Retrains nightly when your labels change.")

stats = api_get("/labels/stats")
meta = api_get("/model/status")
names = {int(k): v for k, v in meta.get("names", {}).items()}
need, have = stats["needed_for_model"], stats["labeled_windows_approx"]

acc = meta.get("accuracy") or {}
with st.container(horizontal=True, wrap=True, gap="small"):
    st.metric("Labeled blocks", f"{have} / {need}", border=True, width=170)
    st.metric("Model", "serving" if meta.get("serving") else "off", border=True, width=170)
    st.metric("Routines accuracy", f"{acc['routines']:.0%}" if acc.get("routines") is not None else "—",
              help="on held-out later days", border=True, width=170)
    st.metric("With model", f"{acc['combined']:.0%}" if acc.get("combined") is not None else "—",
              f"{(acc['combined'] - acc['routines']) * 100:+.0f} pts" if acc.get("combined") is not None else None,
              border=True, width=170)

if have < need:
    st.progress(have / need, text=f"Label {need - have} more blocks to start the model. Fastest way: Corrections → "
                                  "fix wrong rows → Confirm this day.")
if meta.get("trained_at"):
    st.write(f"Last trained {fmt_dt(meta['trained_at'])} on {meta['n_labeled']} labeled blocks"
             + (f" · {meta['note']}" if meta.get("note") else "")
             + (" · **labels changed since then**" if meta.get("labels_changed_since_training") else ""))
if st.button("Train now"):
    with st.spinner("Training…"):
        api_send("POST", "/model/train")
    st.rerun()

metrics = meta.get("metrics") or {}
if metrics:
    rows = []
    for cid, m in metrics.items():
        rows.append({"Category": names.get(int(cid), cid), "Model used": "✓" if m["enabled"] else "",
                     "Blocks": m["model"]["support"],
                     "Routines precision": m["routines"]["precision"], "Routines recall": m["routines"]["recall"],
                     "Model precision": m["model"]["precision"], "Model recall": m["model"]["recall"],
                     "Routines F1": m["routines"]["f1"], "Model F1": m["model"]["f1"]})
    df = pd.DataFrame(rows).sort_values("Blocks", ascending=False)

    fig = go.Figure()
    fig.add_bar(y=df["Category"], x=df["Routines F1"], name="Routines", orientation="h", marker_color="#A5A5A5",
                hovertemplate="%{y}: routines F1 %{x:.2f}<extra></extra>")
    fig.add_bar(y=df["Category"], x=df["Model F1"], name="Model", orientation="h", marker_color="#6E98D2",
                hovertemplate="%{y}: model F1 %{x:.2f}<extra></extra>")
    fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.1, height=120 + 46 * len(df),
                      margin=dict(l=8, r=8, t=70, b=8),
                      title=dict(text="F1 per category (held-out days)", font=dict(size=14), y=0.98, yanchor="top"),
                      xaxis=dict(range=[0, 1], tickformat=".0%"), yaxis=dict(autorange="reversed"),
                      legend=dict(orientation="h", x=0, y=1.0, yanchor="bottom"))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    st.dataframe(df, hide_index=True, width="stretch")
    st.caption("Precision: when it says X, how often it is right. Recall: of your real X blocks, how many it finds. "
               "'Model used' = the model beat the routines for that category on later days.")
