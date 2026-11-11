"""Knowledge graph: cache-hit stats, review queue, browse/edit classifications of unknown items."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import api_get, api_send, fmt_dt, setup_page

setup_page("Knowledge graph")
st.title("Knowledge graph")
st.caption("Items your rules don't know are classified here: exact match → same channel/site → similar title → "
           "LLM (title, site and channel name only). Every answer is stored, so the LLM is asked once per item.")

stats = api_get("/kg/stats")
week = api_get("/kg/stats", days=7)
sig = api_get("/signals")
signals = sig["signals"]

# ---- headline numbers -------------------------------------------------------------------------------------
rate = stats["cache_hit_rate"]
review = api_get("/kg/review")
with st.container(horizontal=True, wrap=True, gap="small"):
    st.metric("Served without LLM", f"{rate:.0%}" if rate is not None else "—", border=True, width=170,
              help="exact + channel/site + similar-title hits, divided by those plus LLM answers")
    st.metric("LLM calls saved", f"{stats['llm_calls_saved']:,}", f"{week['llm_calls_saved']:,} this week",
              delta_color="off", delta_arrow="off", border=True, width=170)
    st.metric("LLM calls today", f"{stats['llm_calls_today']} / {stats['daily_budget']}", border=True, width=170)
    st.metric("Items in graph", f"{sum(stats['nodes'].values()):,}", border=True, width=170)
    st.metric("Needs review", len(review), border=True, width=170)

status = []
status.append(f"LLM: **{stats['provider']}**" if stats["llm_ready"] else "LLM: **no API key** (items wait in the queue)")
if stats["dry_run"]:
    status.append("**dry-run on** (LLM never called)")
status.append(f"similarity model: {stats['embedder'] or 'off'}")
q = stats["queue"]
status.append(f"queue: {q.get('pending', 0)} pending, {q.get('failed', 0)} failed")
st.write(" · ".join(status))

order = ["exact", "parent", "semantic", "llm", "queued", "local-only"]
names = {"exact": "Exact item", "parent": "Same channel / site", "semantic": "Similar title", "llm": "Asked LLM",
         "queued": "Queued (LLM unavailable)", "local-only": "Search (never sent, local only)"}
counts = [stats["lookups"].get(k, 0) for k in order]
if sum(counts):
    fig = go.Figure(go.Bar(
        y=[names[k] for k in order], x=counts, orientation="h", marker_color="#6E98D2",
        text=counts, textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: %{x}<extra></extra>",
    ))
    fig.update_layout(height=230, margin=dict(l=8, r=40, t=24, b=8), title=dict(text="How lookups were answered (all time)",
                      font=dict(size=14)), yaxis=dict(autorange="reversed"), xaxis=dict(showgrid=False, visible=False))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

if st.button("Process queue now"):
    st.write(api_send("POST", "/kg/process"))
    st.rerun()

tab_review, tab_browse, tab_log = st.tabs([f"Review queue ({len(review)})", "Browse & edit", "Recent lookups"])


def editor(item: dict, key: str) -> None:
    """Signal picker + 'apply to whole channel/site' + save / remove / retry."""
    where = item["channel"] or item["domain"]
    parent_label = "Apply to the whole channel" if item["channel"] else "Apply to the whole site"
    a, b, c = st.columns([2, 2, 1])
    current = item["signal"] if item["signal"] in signals else "other"
    new = a.selectbox("Signal", signals, index=signals.index(current), key=f"s-{key}")
    apply = b.checkbox(parent_label, key=f"p-{key}", disabled=item["type"] in ("app", "title", "search") or not where,
                       help="New items from the same channel/site will inherit this signal.")
    if c.button("Save", key=f"save-{key}", type="primary"):
        api_send("POST", "/kg/correct", {"node_id": item["node_id"], "signal": new, "apply_to_parent": apply})
        st.rerun()
    d, e, _ = st.columns([1, 1, 3])
    if item["edge_id"] and item["source"] != "parent" and d.button("Remove classification", key=f"rm-{key}",
                                                                     help="Kept in history; the item becomes unknown."):
        api_send("POST", f"/kg/edges/{item['edge_id']}/deactivate")
        st.rerun()
    if item["queue_status"] == "failed" and e.button("Retry LLM", key=f"rt-{key}"):
        api_send("POST", f"/kg/nodes/{item['node_id']}/retry")
        st.rerun()


def describe(item: dict) -> str:
    kind = {"video": "YouTube video", "page": "web page", "title": "browser tab", "search": "search", "app": "app"}.get(item["type"],
                                                                                                   item["type"])
    where = " · ".join(x for x in (item["channel"], item["domain"]) if x)
    if item["signal"]:
        verdict = f"**{item['signal']}** ({item['confidence']:.2f}, {item['source']})"
        if item["reason"]:
            verdict += f" — {item['reason']}"
    else:
        verdict = f"**unknown** ({item['queue_status'] or 'not queued'}" + \
                  (f": {item['queue_error']}" if item["queue_error"] else "") + ")"
    return f"{kind} · {where}  \n{verdict}"


with tab_review:
    if not review:
        st.success("Nothing to review.")
    for it in review[:50]:
        with st.container(border=True):
            st.markdown(f"**{it['label']}**  \n{describe(it)}")
            editor(it, f"r{it['node_id']}")

with tab_browse:
    a, b = st.columns([3, 1])
    q = a.text_input("Search title / site / app")
    t = b.selectbox("Type", ["", "video", "page", "search", "title", "app", "channel", "path", "domain"])
    nodes = api_get("/kg/nodes", q=q or None, type=t or None, limit=300)
    if nodes:
        df = pd.DataFrame([{
            "id": n["node_id"], "type": n["type"], "item": n["label"], "channel/site": n["channel"] or n["domain"],
            "signal": n["signal"] or "unknown", "conf": n["confidence"], "source": n["source"],
            "seen": n["seen_count"], "last seen": fmt_dt(n["last_seen"], "%b %d %H:%M"),
        } for n in nodes])
        st.dataframe(df, hide_index=True, width="stretch")
        pick = st.selectbox("Edit item", nodes, format_func=lambda n: f"{n['node_id']} · {n['type']} · {n['label'][:80]}")
        if pick:
            st.markdown(describe(pick))
            editor(pick, f"b{pick['node_id']}")
    else:
        st.info("No items yet.")

with tab_log:
    rows = api_get("/kg/lookups", limit=200)
    if rows:
        st.dataframe(pd.DataFrame([{
            "time": fmt_dt(r["ts"], "%b %d %H:%M:%S"), "outcome": r["outcome"], "type": r["type"],
            "item": r["label"], "signal": r["signal"], "conf": r["confidence"], "detail": r["detail"],
        } for r in rows]), hide_index=True, width="stretch")
    else:
        st.info("No lookups yet.")
