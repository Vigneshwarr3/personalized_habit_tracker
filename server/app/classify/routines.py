"""Stage 4: routines.yaml -> category name for a window's features. Small, safe condition language
(no eval): "<feature> [+ <feature> ...] <op> <number>"."""
from __future__ import annotations

import operator
import re
from dataclasses import dataclass

FEATURES = {
    "active_minutes", "idle_ratio", "num_contexts", "largest_context_share",
    "switch_rate", "media_share", "run_minutes",
}
_OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt, "==": operator.eq}
_COND = re.compile(r"^\s*(?P<lhs>.+?)\s*(?P<op>>=|<=|==|>|<)\s*(?P<num>-?\d+(?:\.\d+)?)\s*$")
_TERM = re.compile(r"^(signals\.[A-Za-z0-9_]+|[a-z_]+)$")


@dataclass(frozen=True)
class Condition:
    terms: tuple[str, ...]
    op: str
    value: float
    text: str

    def holds(self, features: dict) -> bool:
        signals = features.get("signals", {})
        total = 0.0
        for t in self.terms:
            if t.startswith("signals."):
                total += float(signals.get(t[8:], 0.0))
            else:
                total += float(features.get(t, 0.0))
        return _OPS[self.op](round(total, 9), self.value)


def parse_condition(text: str, signals: list[str]) -> Condition:
    m = _COND.match(str(text))
    if not m:
        raise ValueError(f"bad routine condition {text!r} (expected e.g. 'signals.education >= 0.6')")
    terms = tuple(t.strip() for t in m["lhs"].split("+"))
    for t in terms:
        if not _TERM.match(t):
            raise ValueError(f"bad term {t!r} in condition {text!r}")
        if t.startswith("signals."):
            if t[8:] not in signals:
                raise ValueError(f"unknown signal {t[8:]!r} in condition {text!r} (add it to settings.yaml)")
        elif t not in FEATURES:
            raise ValueError(f"unknown feature {t!r} in condition {text!r}; known: {sorted(FEATURES)}")
    return Condition(terms, m["op"], float(m["num"]), str(text))


@dataclass(frozen=True)
class Routine:
    name: str
    category: str
    when: tuple[Condition, ...]
    when_any: tuple[Condition, ...]

    def matches(self, features: dict) -> bool:
        if not all(c.holds(features) for c in self.when):
            return False
        if self.when_any and not any(c.holds(features) for c in self.when_any):
            return False
        return True


def load_routines(raw: list[dict], signals: list[str]) -> list[Routine]:
    out = []
    for i, r in enumerate(raw):
        if not r.get("category"):
            raise ValueError(f"routines.yaml #{i + 1}: missing category")
        when = tuple(parse_condition(c, signals) for c in (r.get("when") or []))
        when_any = tuple(parse_condition(c, signals) for c in (r.get("when_any") or []))
        if not when and not when_any:
            raise ValueError(f"routines.yaml #{i + 1}: needs `when` or `when_any`")
        out.append(Routine(str(r.get("name") or f"routine {i + 1}"), str(r["category"]), when, when_any))
    return out


def first_match(routines: list[Routine], features: dict) -> Routine | None:
    for r in routines:
        if r.matches(features):
            return r
    return None
