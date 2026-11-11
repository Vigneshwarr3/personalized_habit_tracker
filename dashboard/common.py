"""Shared dashboard helpers: API client, owner check, time helpers."""
from __future__ import annotations

import os
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

import httpx
import streamlit as st

API_URL = os.environ.get("HT_API_URL", "http://127.0.0.1:8765").rstrip("/")
ADMIN_TOKEN = os.environ.get("HT_ADMIN_TOKEN", "")
OWNER = os.environ.get("HT_OWNER_LOGIN", "").strip().lower()


def setup_page(title: str) -> None:
    """Per-page setup. Page config + navigation bar live in app.py (the entry point)."""
    st.set_page_config(page_title=f"{title} · Habit Tracker", page_icon="🗓️", layout="wide")
    guard_owner()


def guard_owner() -> None:
    """Behind `tailscale serve`, Tailscale adds the visitor's login as a header (it can't be forged by clients).
    If HT_OWNER_LOGIN is set, only that login may see the dashboard."""
    if not OWNER:
        return
    login = (st.context.headers.get("Tailscale-User-Login") or "").strip().lower()
    if login != OWNER:
        st.error("This dashboard is private.")
        st.stop()


def in_app() -> bool:
    """True inside the Habit Tracker Android app (it adds 'HabitTrackerApp/<version>' to its user agent)."""
    return "HabitTrackerApp" in (st.context.headers.get("User-Agent") or "")


def is_mobile() -> bool:
    ua = (st.context.headers.get("User-Agent") or "").lower()
    return "mobile" in ua or "android" in ua or "iphone" in ua


def _client() -> httpx.Client:
    return httpx.Client(base_url=API_URL, timeout=60, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})


def api_get(path: str, **params):
    return _cached_get(path, tuple(sorted((k, str(v)) for k, v in params.items() if v is not None)))


@st.cache_data(ttl=30, show_spinner=False)
def _cached_get(path: str, params: tuple):
    with _client() as c:
        r = c.get(path, params=dict(params))
    if r.status_code != 200:
        raise RuntimeError(f"GET {path} -> {r.status_code}: {r.text[:300]}")
    return r.json()


def api_send(method: str, path: str, json=None):
    with _client() as c:
        r = c.request(method, path, json=json)
    _cached_get.clear()
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except Exception:
            detail = r.text
        raise RuntimeError(f"{detail}")
    return r.json()


def tz() -> ZoneInfo:
    return ZoneInfo(api_get("/status")["timezone"])


def today() -> date:
    return datetime.now(tz()).date()


def local_ts(d: date, t: dtime) -> float:
    return datetime.combine(d, t, tz()).timestamp()


def fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz()).strftime("%H:%M")


def fmt_dt(ts: float | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return datetime.fromtimestamp(ts, tz()).strftime(fmt) if ts else "never"


def api_down_notice(e: Exception) -> None:
    st.error(f"Can't reach the Habit Tracker API at {API_URL}: {e}")
    st.stop()
