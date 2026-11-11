"""Stage 2: rules.yaml -> signal for a single activity item (app / domain / url / title)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class SignalHit:
    signal: str
    confidence: float
    source: str          # "rule" | "none" (later: "kg-exact", "llm", "user", ...)
    rule_index: int | None = None


UNKNOWN = SignalHit("unknown", 0.0, "none", None)


def _as_list(v) -> list[str]:
    if v is None:
        return []
    return [str(x) for x in (v if isinstance(v, list) else [v])]


class RuleEngine:
    def __init__(self, rules: list[dict], signals: list[str]):
        allowed = set(signals)
        self._rules = []
        for i, r in enumerate(rules):
            sig = r.get("signal")
            if sig not in allowed:
                raise ValueError(f"rules.yaml rule #{i + 1}: signal {sig!r} is not in settings.yaml signals")
            known = {"signal", "app", "domain", "url_regex", "title_regex", "confidence"}
            extra = set(r) - known
            if extra:
                raise ValueError(f"rules.yaml rule #{i + 1}: unknown keys {sorted(extra)}")
            if not (set(r) & {"app", "domain", "url_regex", "title_regex"}):
                raise ValueError(f"rules.yaml rule #{i + 1}: needs at least one of app/domain/url_regex/title_regex")
            self._rules.append(
                {
                    "signal": sig,
                    "confidence": float(r.get("confidence", 1.0)),
                    "apps": {a.lower() for a in _as_list(r.get("app"))},
                    "domains": [d.lower().lstrip(".") for d in _as_list(r.get("domain"))],
                    "url_re": re.compile(r["url_regex"], re.I) if r.get("url_regex") else None,
                    "title_re": re.compile(r["title_regex"], re.I) if r.get("title_regex") else None,
                }
            )
        self.match = lru_cache(maxsize=50_000)(self._match)

    @staticmethod
    def _domain_ok(domain: str | None, wanted: list[str]) -> bool:
        if not domain:
            return False
        d = domain.lower()
        return any(d == w or d.endswith("." + w) for w in wanted)

    def _match(self, app: str | None, domain: str | None, url: str | None, title: str | None) -> SignalHit:
        app_l = (app or "").lower()
        for i, r in enumerate(self._rules):
            if r["apps"] and app_l not in r["apps"]:
                continue
            if r["domains"] and not self._domain_ok(domain, r["domains"]):
                continue
            if r["url_re"] is not None and not (url and r["url_re"].search(url)):
                continue
            if r["title_re"] is not None and not (title and r["title_re"].search(title)):
                continue
            return SignalHit(r["signal"], r["confidence"], "rule", i)
        return UNKNOWN
