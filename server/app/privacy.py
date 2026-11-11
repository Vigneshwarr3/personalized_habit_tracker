"""Sensitive-activity filter (server side; the Mac agent applies the same rules before upload).

For a private app, a private domain, or a sensitive-looking title:
  * the window/page TITLE is dropped
  * the URL is cut to the bare site (https://secure.chase.com/)
  * the item is never put in the knowledge graph, so it never reaches the LLM
The time still counts (e.g. bank sites -> 'admin' signal -> Bank/University/Hospital/Others).
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}


def _match(value: str, patterns: list[str]) -> bool:
    v = value.lower()
    for p in patterns:
        p = p.lower()
        if fnmatch.fnmatchcase(v, p) or ("*" not in p and v.endswith("." + p)):
            return True
    return False


@dataclass
class Privacy:
    private_apps: list[str]
    private_domains: list[str]
    title_re: re.Pattern | None

    @classmethod
    def from_settings(cls, settings: dict) -> "Privacy":
        p = settings.get("privacy", {}) or {}
        rx = p.get("private_title_regex")
        return cls(list(p.get("private_apps", [])), list(p.get("private_domains", [])),
                   re.compile(rx, re.I) if rx else None)

    def as_dict(self) -> dict:
        return {"private_apps": self.private_apps, "private_domains": self.private_domains,
                "private_title_regex": self.title_re.pattern if self.title_re else None}

    def app_is_private(self, app: str | None) -> bool:
        return bool(app) and _match(app, self.private_apps)

    def domain_is_private(self, domain: str | None) -> bool:
        return bool(domain) and _match(domain, self.private_domains)

    def title_is_sensitive(self, title: str | None, domain: str | None) -> bool:
        if not title or self.title_re is None:
            return False
        if domain and (domain in YOUTUBE_HOSTS or domain.endswith(".youtube.com")):
            return False  # a video called "How to build a login page" is not sensitive
        return bool(self.title_re.search(title))

    def is_private(self, app: str | None, domain: str | None, title: str | None) -> bool:
        return self.app_is_private(app) or self.domain_is_private(domain) or self.title_is_sensitive(title, domain)

    def redact(self, row: dict) -> dict:
        """row has app/title/url/domain (url already sanitized). Returns a copy with sensitive parts removed."""
        if not self.is_private(row.get("app"), row.get("domain"), row.get("title")):
            return row
        out = dict(row, title=None)
        if out.get("url"):
            p = urlsplit(out["url"])
            out["url"] = f"{p.scheme}://{p.netloc}/"
        return out
