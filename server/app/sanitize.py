"""URL sanitization. Runs on the server for every heartbeat, so collectors can't leak query strings by mistake.

Rules:
* only http/https URLs are kept (file://, chrome://, about: ... are dropped entirely)
* user:password@, query string, fragment and ;path-params are removed
* the ONLY query parameter ever kept is v=<id> on youtube.com/watch
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")


def domain_of(host: str) -> str:
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def sanitize_url(url: str | None) -> tuple[str | None, str | None]:
    """Return (sanitized_url, domain). Both None if the URL is unusable or not web."""
    if not url or not isinstance(url, str):
        return None, None
    try:
        parts = urlsplit(url.strip())
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        return None, None
    if scheme not in ("http", "https") or not host:
        return None, None

    path = parts.path or "/"
    path = path.split(";", 1)[0] or "/"
    netloc = host + (f":{port}" if port else "")

    query = ""
    if host in YOUTUBE_HOSTS and path == "/watch":
        vid = (parse_qs(parts.query).get("v") or [None])[0]
        if vid and _VIDEO_ID.match(vid):
            query = f"?v={vid}"

    return f"{scheme}://{netloc}{path}{query}", domain_of(host)
