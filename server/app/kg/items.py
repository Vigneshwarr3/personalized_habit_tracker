"""Turns an activity segment into a knowledge-graph ITEM: the exact node it maps to plus its parents.

  YouTube video     -> video:<id>                  parents: channel (found later via oEmbed) -> domain
  other web page    -> page:<domain>/<path>         parents: path:<domain>/<first segment> -> domain
  browser, no URL   -> title:<app>|<title>          (automation denied / Firefox)
  any other app     -> app:<name>                   (app NAME only; window titles of apps never leave the server)
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from ..sanitize import YOUTUBE_HOSTS

_NOTIF = re.compile(r"^\(\d+\+?\)\s*")
_YT_SUFFIX = re.compile(r"\s*[-–|]\s*YouTube\s*$", re.I)
_URLS = re.compile(r"https?://\S+|www\.\S+", re.I)
_VID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")


def normalize_title(title: str | None) -> str | None:
    if not title:
        return None
    t = _NOTIF.sub("", title.strip())
    t = _YT_SUFFIX.sub("", t)
    t = _URLS.sub("", t)
    t = " ".join(t.split())
    return t[:300] or None


def youtube_video_id(url: str | None) -> str | None:
    if not url:
        return None
    p = urlsplit(url)
    host = (p.hostname or "").lower()
    if host in YOUTUBE_HOSTS and p.path == "/watch":
        v = (parse_qs(p.query).get("v") or [None])[0]
        return v if v and _VID.match(v) else None
    if host == "youtu.be":
        v = p.path.strip("/")
        return v if _VID.match(v) else None
    if host in YOUTUBE_HOSTS and p.path.startswith("/shorts/"):
        v = p.path.split("/")[2] if len(p.path.split("/")) > 2 else ""
        return v if _VID.match(v) else None
    return None


@dataclass
class Item:
    type: str                  # video | page | title | search | app
    key: str
    label: str | None          # human-readable (title or app name)
    title: str | None          # normalized title used for similarity + LLM
    domain: str | None = None
    app: str | None = None
    parents: list[tuple[str, str, str]] = field(default_factory=list)  # (type, key, label), nearest first
    local_only: bool = False   # never sent to the LLM (search pages: the title is your search words)


def matches_page_pattern(domain: str, path: str, patterns: list[str] | tuple[str, ...]) -> bool:
    """Patterns look like 'google.*/search' or 'duckduckgo.com': domain glob + optional path prefix."""
    for p in patterns:
        dom, _, prefix = p.partition("/")
        if (fnmatch.fnmatch(domain, dom) or domain.endswith("." + dom)) and path.startswith("/" + prefix if prefix else "/"):
            return True
    return False


def item_from_segment(seg: dict, browsers: set[str], search_patterns: list[str] | tuple[str, ...] = ()) -> Item | None:
    app = seg.get("app") or ""
    url, domain = seg.get("url"), seg.get("domain")
    title = normalize_title(seg.get("title"))
    if url and domain:
        vid = youtube_video_id(url)
        if vid:
            return Item("video", vid, title or vid, title, "youtube.com", app,
                        parents=[("domain", "youtube.com", "youtube.com")])
        path = urlsplit(url).path
        if search_patterns and matches_page_pattern(domain, path, search_patterns):
            # The query string is stripped, so every search shares one URL; identify each search by its title.
            if not title:
                return None
            return Item("search", f"{domain}|{title.lower()}", title, title, domain, app, local_only=True)
        segs = [s for s in path.split("/") if s]
        key = domain + ("/" + "/".join(segs) if segs else "/")
        parents = []
        if segs:
            parents.append(("path", f"{domain}/{segs[0]}", f"{domain}/{segs[0]}"))
        parents.append(("domain", domain, domain))
        return Item("page", key, title or key, title, domain, app, parents)
    if app.lower() in browsers:
        if not title:
            return None
        return Item("title", f"{app.lower()}|{title.lower()}", title, title, None, app)
    if not app:
        return None
    return Item("app", app.lower(), app, None, None, app)
