"""Overview: summary cards, hour-by-day heatmap (rows = hours, columns = dates) and hours per category."""
from __future__ import annotations

import html
from datetime import date, time as dtime, timedelta

import altair as alt
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import api_down_notice, api_get, api_send, fmt_time, is_mobile, local_ts, setup_page, today

setup_page("Overview")
UNCLASSIFIED_COLOR = "#C9B6E4"  # distinct from all 17 seeded colors; blank cells = no data
MAX_ROUNDED_DAYS = 45           # beyond this, plain squares (hundreds of rounded shapes get slow)

mobile = is_mobile()
dark = getattr(getattr(st.context, "theme", None), "type", "light") == "dark"
SURFACE = "#121417" if dark else "#FFFFFF"
EMPTY = "rgba(255,255,255,0.05)" if dark else "rgba(20,24,30,0.045)"
TODAY_BG = "rgba(255,255,255,0.07)" if dark else "rgba(20,24,30,0.06)"

try:
    end_default = today()
except Exception as e:  # noqa: BLE001
    api_down_notice(e)

# ---- filters --------------------------------------------------------------------------------------
c1, c2, c3 = st.columns([2, 3, 2], vertical_alignment="bottom")
preset = c1.selectbox("Range", ["Last 7 days", "Last 14 days", "Last 30 days", "This month", "Custom"], index=0)
if preset == "Custom":
    rng = c2.date_input("Dates", (end_default - timedelta(days=6), end_default))
    start, end = (rng if isinstance(rng, tuple) and len(rng) == 2 else (end_default, end_default))
elif preset == "This month":
    start, end = end_default.replace(day=1), end_default
else:
    n = int(preset.split()[1])
    start, end = end_default - timedelta(days=n - 1), end_default
if preset != "Custom":
    c2.caption(f"{start:%b %d} – {end:%b %d, %Y}")
if api_get("/status")["paused"]:
    c3.warning("Tracking is paused", icon=":material/pause_circle:")

data = api_get("/dashboard", start=start.isoformat(), end=end.isoformat())
dates: list[str] = data["dates"]
cells = data["heatmap"]
summary = data["summary"]
rows = summary["categories"]

# ---- cards (choose which in Settings) ------------------------------------------------------------
k = api_get("/kpis", start=start.isoformat(), end=end.isoformat())
show = k["cards"]
cmp = show.get("change_vs_previous", True)


def delta_h(cur: float, prev: float) -> str | None:
    d = cur - prev
    return None if abs(d) < 0.05 else f"{d:+.1f} h"


def list_card(title: str, items: list[tuple[str | None, str, float, float]], compare: bool | None = None) -> None:
    """A bordered card listing (color, name, hours, previous hours) rows."""
    cmp_ = cmp if compare is None else compare
    rows_html = "".join(
        f"<div style='display:flex;align-items:center;gap:8px;margin:5px 0'>"
        + (f"<span style='flex:none;width:10px;height:10px;border-radius:3px;background:{color}'></span>" if color else "")
        + f"<span style='flex:1;min-width:0'>{html.escape(name)}</span><b style='white-space:nowrap'>{hours:g} h</b>"
        + (f"<span style='opacity:.6;font-size:.8rem;min-width:62px;text-align:right'>"
           f"{hours - prev:+.1f}</span>" if cmp_ else "")
        + "</div>"
        for color, name, hours, prev in items
    )
    with st.container(border=True, width="stretch" if mobile else 340):
        st.markdown(f"**{title}**" + (" <span style='opacity:.6;font-size:.8rem'>· vs previous</span>" if cmp_ else ""),
                    unsafe_allow_html=True)
        st.html(rows_html or "<span style='opacity:.6'>No data yet</span>")


# list cards: side by side on wide screens, stacked full-width on phones
with st.container(horizontal=not mobile, wrap=True, gap="small"):
    if show.get("top3", True):
        list_card("Top 3", [(t["color"], t["short_name"], t["hours"], t["prev_hours"]) for t in k["top3"]])
    if show.get("group_totals", True):
        list_card("Activities", [(g["color"], g["group"], g["hours"], g["prev_hours"])
                             for g in sorted(k["group_totals"], key=lambda g: -g["hours"])])
    if show.get("phone_vs_laptop", True):
        pl = k["phone_vs_laptop"]
        list_card("Phone vs laptop", [(None, "Phone", round(pl["phone_hours"], 1), 0),
                                      (None, "Laptop", round(pl["laptop_hours"], 1), 0)], compare=False)

# number cards: wrap two per row on phones
with st.container(horizontal=True, wrap=True, gap="small"):
    if show.get("time_to_cut", True) and k["time_to_cut"]:
        t = k["time_to_cut"]
        st.metric("Time to cut", f"{t['hours']:g} h", delta_h(t["hours"], t["prev_hours"]) if cmp else None,
                  delta_color="inverse", border=True, width=170, help=f"group: {t['group']} (change in Settings)")
    if show.get("coverage", True):
        cov = k["coverage"]
        st.metric("Day coverage", f"{cov['percent']:.0f}%" if cov["percent"] is not None else "—", border=True,
                  width=170, help=f"{cov['filled_blocks']} of {cov['total_blocks']} 15-min blocks so far have a category")
    if show.get("commute", True):
        cm = k["commute"]
        st.metric("Commute time", f"{cm['hours']:g} h", delta_h(cm["hours"], cm["prev_hours"]) if cmp else None,
                  delta_color="off", border=True, width=170,
                  help="All time on the move near home, including when you were also doing something on a device")
    if show.get("focus", True):
        f = k["focus"]
        when = None
        if f["start_ts"]:
            when = f"{date.fromisoformat(f['date']):%a} {fmt_time(f['start_ts'])}–{fmt_time(f['end_ts'])}"
        st.metric("Longest focus", f"{f['hours']:g} h", when, delta_color="off", delta_arrow="off", border=True,
                  width=170, help="Longest unbroken run of focus categories" +
                  (f" ({date.fromisoformat(f['date']):%b %d})" if f["start_ts"] else "") + ". Choose them in Settings.")
    if show.get("streak", True):
        s_ = k["streak"]
        st.metric("Streak", f"{s_['current_days']} days", f"best {s_['best_days']}", delta_color="off",
                  delta_arrow="off", border=True, width=170,
                  help=f"Days in a row with {s_['rule']}." + (" Today doesn't count yet." if s_["today_pending"] else ""))

# ---- heatmap --------------------------------------------------------------------------------------
present = []  # categories in view, fixed by category id order (color follows the category, not its rank)
seen = set()
for c in sorted(cells, key=lambda c: (c["category_id"] is None, c["category_id"] or 0)):
    if c["category"] not in seen:
        seen.add(c["category"])
        present.append((c["category"], c["color"] or UNCLASSIFIED_COLOR))
color_of = dict(present)

n_days = len(dates)
col = {d: i for i, d in enumerate(dates)}
by_cell = {(c["hour"], col[c["date"]]): c for c in cells if c["date"] in col}
today_iso = end_default.isoformat()


def hover_text(i: int, j: int) -> str:
    c = by_cell.get((i, j))
    if c is None:
        return f"{dates[j]}  {i:02d}:00<br>No data"
    parts = "<br>".join(f"{html.escape(k.split(' › ', 1)[-1])}: {v} min"
                        for k, v in sorted(c["minutes"].items(), key=lambda kv: -kv[1]))
    devs = ", ".join(c.get("devices") or []) or "manual label"
    where = ", ".join(c.get("places") or [])
    return (f"<b>{html.escape(c['category'])}</b><br>{c['date']}  {i:02d}:00–{i + 1:02d}:00<br>{parts}"
            f"<br><i>from: {html.escape(devs)}</i>" + (f"<br><i>where: {html.escape(where)}</i>" if where else ""))


def rounded(x0: float, y0: float, x1: float, y1: float, rx: float, ry: float) -> str:
    return (f"M{x0 + rx},{y0} L{x1 - rx},{y0} Q{x1},{y0} {x1},{y0 + ry} L{x1},{y1 - ry} Q{x1},{y1} {x1 - rx},{y1} "
            f"L{x0 + rx},{y1} Q{x0},{y1} {x0},{y1 - ry} L{x0},{y0 + ry} Q{x0},{y0} {x0 + rx},{y0} Z")


hours = list(range(24))
hover = [[hover_text(i, j) for j in range(n_days)] for i in hours]
fig = go.Figure()
if n_days <= MAX_ROUNDED_DAYS:
    shapes = []
    if today_iso in col:  # today's column, shaded behind the tiles
        j = col[today_iso]
        shapes.append(dict(type="path", path=rounded(j - 0.5, -0.6, j + 0.5, 23.6, 0.15, 0.4), fillcolor=TODAY_BG,
                           line=dict(width=0), layer="below"))
    for i in hours:
        for j in range(n_days):
            c = by_cell.get((i, j))
            fill = (c["color"] or UNCLASSIFIED_COLOR) if c else EMPTY
            shapes.append(dict(type="path", path=rounded(j - 0.42, i - 0.38, j + 0.42, i + 0.38, 0.1, 0.18),
                               fillcolor=fill, line=dict(width=0), layer="below"))
    fig.update_layout(shapes=shapes)
else:  # long ranges: plain colored squares
    names = [name for name, _ in present]
    nk = max(1, len(names))
    scale = []
    for i, (_, color) in enumerate(present):
        scale += [[i / nk, color], [(i + 1) / nk, color]]
    z = [[(names.index(by_cell[(i, j)]["category"]) + 0.5) if (i, j) in by_cell else None for j in range(n_days)]
         for i in hours]
    fig.add_trace(go.Heatmap(z=z, x=list(range(n_days)), y=hours, zmin=0, zmax=nk, colorscale=scale or None,
                             showscale=False, xgap=2, ygap=2, hoverinfo="skip"))

# invisible, selectable point on every hour: hover/tap details + box/lasso selection for painting
SELECTED = "rgba(255,255,255,0.35)" if dark else "rgba(20,24,30,0.30)"
pts = [(j, i) for i in hours for j in range(n_days)]
fig.add_trace(go.Scatter(
    x=[j for j, _ in pts], y=[i for _, i in pts], mode="markers",
    marker=dict(symbol="square", size=12 if mobile else 18, color="rgba(0,0,0,0)"),
    customdata=[[dates[j], i] for j, i in pts], hovertext=[hover[i][j] for j, i in pts],
    hovertemplate="%{hovertext}<extra></extra>",
    selected=dict(marker=dict(color=SELECTED)), unselected=dict(marker=dict(opacity=1)),
))

step = 1 if n_days <= (8 if mobile else 16) else (2 if n_days <= (16 if mobile else 31) else 7)
ticks = list(range(0, n_days, step))
labels = []
for j in ticks:
    d = date.fromisoformat(dates[j])
    lab = d.strftime("%a<br>%d") if n_days <= 16 else d.strftime("%d")
    labels.append(f"<b>{lab}</b>" if dates[j] == today_iso else lab)
fig.update_layout(
    height=600 if not mobile else 560, margin=dict(l=4, r=4, t=4, b=4),
    xaxis=dict(side="top", tickvals=ticks, ticktext=labels, tickfont=dict(size=12), range=[-0.55, n_days - 0.45],
               showgrid=False, zeroline=False, fixedrange=True),
    yaxis=dict(tickvals=hours, ticktext=[f"{h:02d}:00" for h in hours], tickfont=dict(size=12),
               range=[23.6, -0.6], showgrid=False, zeroline=False, fixedrange=True),
    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", showlegend=False,
    dragmode=False if mobile else "select", clickmode="event+select",
)

left, right = st.columns([3, 2], gap="large") if not mobile else (st.container(), st.container())
with left:
    st.subheader("Hour by day")
    ver = st.session_state.setdefault("heat_ver", 0)
    event = st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, on_select="rerun",
                            selection_mode=("points", "box", "lasso"), key=f"heat-{start}-{end}-{ver}")
    picked = sorted({(p["customdata"][0], int(p["customdata"][1]))
                     for p in (event.selection.get("points", []) if event else []) if p.get("customdata")})
    if picked:
        cats = api_get("/categories", include_hidden=False)
        with st.container(border=True):
            st.markdown(f"**Paint {len(picked)} selected hour{'s' if len(picked) > 1 else ''}** as:")
            c1, c2, c3 = st.columns([3, 1, 1], vertical_alignment="bottom")
            pick = c1.selectbox("Category", cats, format_func=lambda c: c["name"], label_visibility="collapsed",
                                key="paint-cat")
            if c2.button("Paint", type="primary", width="stretch"):
                spans: list[list] = []  # merge back-to-back hours of the same day into one label
                for d, h in picked:
                    if spans and spans[-1][0] == d and spans[-1][2] == h:
                        spans[-1][2] = h + 1
                    else:
                        spans.append([d, h, h + 1])
                for d, h0, h1 in spans:
                    day0 = date.fromisoformat(d)
                    a = local_ts(day0, dtime(h0))
                    b = local_ts(day0 + timedelta(days=1), dtime(0)) if h1 == 24 else local_ts(day0, dtime(h1))
                    api_send("POST", "/labels", {"start_ts": a, "end_ts": b, "category": pick["id"], "note": "painted"})
                st.session_state["heat_ver"] = ver + 1
                st.rerun()
            if c3.button("Clear", width="stretch"):
                st.session_state["heat_ver"] = ver + 1
                st.rerun()
    else:
        st.caption("Tap an hour to paint it." if mobile else
                   "Paint: drag a box over hours (or click one), then choose a category.")
    if present:
        chips = " ".join(
            f'<span style="display:inline-flex;align-items:center;margin:3px 12px 3px 0;font-size:0.85rem">'
            f'<span style="width:12px;height:12px;border-radius:4px;background:{color};margin-right:6px"></span>'
            f'{html.escape(name)}</span>'
            for name, color in present
        )
        st.markdown(chips, unsafe_allow_html=True)

# ---- hours per activity: tap a slice to see its sub-activities ---------------------------------------
def pie_legend(items: list[tuple[str, str, float]]) -> None:
    """The 'N Hours of X, Y%' list under a pie (text in normal ink; the swatch carries the color)."""
    total = sum(h for _, _, h in items) or 1
    st.html("".join(
        f"<div style='display:flex;align-items:center;gap:8px;margin:4px 0;font-size:.9rem'>"
        f"<span style='flex:none;width:11px;height:11px;border-radius:3px;background:{c}'></span>"
        f"{html.escape(f'{h:g} Hours of {n}, {round(100 * h / total)}%')}</div>" for n, c, h in items))


def pie_chart(items: list[tuple[str, str, float]], selectable: bool) -> alt.Chart:
    """items: (name, color, hours). Legend labels read 'N Hours of X, Y%'."""
    total = sum(h for _, _, h in items) or 1
    df = pd.DataFrame([{"name": n, "hours": h, "order": i,
                        "label": f"{h:g} Hours of {n}, {round(100 * h / total)}%"} for i, (n, _, h) in enumerate(items)])
    chart = alt.Chart(df).mark_arc(stroke=SURFACE, strokeWidth=2, outerRadius=120).encode(
        theta=alt.Theta("hours:Q", stack=True),
        color=alt.Color("label:N", sort=df["label"].tolist(),
                        scale=alt.Scale(domain=df["label"].tolist(), range=[c for _, c, _ in items]), legend=None),
        order=alt.Order("order:Q"),
        tooltip=[alt.Tooltip("label:N", title=" ")],
    ).properties(height=260)
    if selectable:
        pick_param = alt.selection_point(fields=["name"], name="pick")
        chart = chart.add_params(pick_param).encode(
            opacity=alt.condition(pick_param, alt.value(1.0), alt.value(0.45)))
    return chart


with right:
    st.subheader("Hours per activity")
    if not rows:
        st.info("No classified time in this range yet.")
    else:
        acts: dict = {}
        for r in rows:
            key = r.get("activity") or r["category"]
            a = acts.setdefault(key, {"color": r.get("activity_color") or r["color"], "hours": 0.0, "subs": []})
            a["hours"] += r["hours"]
            a["subs"].append((r["category"].split(" › ", 1)[-1], r["color"], r["hours"]))
        order = sorted(acts, key=lambda n: -acts[n]["hours"])
        main_items = [(n, acts[n]["color"], round(acts[n]["hours"], 2)) for n in order]
        ev = st.altair_chart(pie_chart(main_items, True), width="stretch", on_select="rerun", selection_mode="pick",
                             key=f"pie-{start}-{end}")
        pie_legend(main_items)
        picked = (ev.selection.get("pick") or [{}]) if ev else [{}]
        pick = picked[0].get("name") if picked else None
        if pick in acts:
            subs = sorted(acts[pick]["subs"], key=lambda x: -x[2])
            st.markdown(f"**{pick}** · {acts[pick]['hours']:g} h")
            st.altair_chart(pie_chart(subs, False), width="stretch")
            pie_legend(subs)
        else:
            st.caption("Tap a slice to see its sub-activities. Tap it again to go back.")
