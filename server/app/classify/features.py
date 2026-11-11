"""Per-window features. All device streams are merged; idle time is dropped.

Features (per 15-min window):
  active_s / active_minutes   union of non-idle time across devices
  idle_ratio                  1 - active_s / window
  num_contexts                contexts (app, or browser+domain) using >= context_min_share of the window's time
  largest_context(_share)     biggest single context and its share
  switch_rate                 context switches per active minute (per device, then summed)
  media_share                 share of time where media played with no keyboard/mouse input
  signals                     {signal: share of time}
  signal_confidence           time-weighted confidence of the signals
  run_minutes                 filled in afterwards: length of the run of adjacent windows sharing the same largest context
"""
from __future__ import annotations

from collections import defaultdict
from typing import Callable

from ..appnames import pretty
from .rules import SignalHit


def context_key(app: str, domain: str | None, browsers: set[str]) -> str:
    if domain and app.lower() in browsers:
        return f"{app} · {domain}"
    return app


def _union_len(intervals: list[tuple[float, float]]) -> float:
    total = 0.0
    cur_a = cur_b = None
    for a, b in sorted(intervals):
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        total += cur_b - cur_a
    return total


def bucket_segments(segments: list[dict], start_ts: float, end_ts: float, window_s: int) -> dict[int, list]:
    buckets: dict[int, list] = defaultdict(list)
    for s in segments:
        a, b = max(s["start_ts"], start_ts), min(s["end_ts"], end_ts)
        if b <= a:
            continue
        i0 = int((a - start_ts) // window_s)
        i1 = int((b - start_ts - 1e-9) // window_s)
        for i in range(i0, i1 + 1):
            w0 = start_ts + i * window_s
            ca, cb = max(a, w0), min(b, w0 + window_s)
            if cb > ca:
                buckets[i].append((s, ca, cb))
    return buckets


def window_features(
    items: list[tuple[dict, float, float]],
    window_s: int,
    browsers: set[str],
    ctx_min_share: float,
    signal_of: Callable[[dict], SignalHit],
) -> dict:
    active = [(a, b) for s, a, b in items if not s["idle"]]
    active_s = _union_len(active)

    ctx_secs: dict[str, float] = defaultdict(float)
    ctx_signal: dict[str, str] = {}
    sig_secs: dict[str, float] = defaultdict(float)
    conf_sum = media_s = 0.0
    titles: dict[tuple, float] = defaultdict(float)
    per_device: dict[str, list] = defaultdict(list)
    devices = set()
    src_secs: dict[str, float] = defaultdict(float)

    for s, a, b in items:
        if s["idle"]:
            continue
        d = b - a
        key = context_key(s["app"], s.get("domain"), browsers)
        hit = signal_of(s)
        ctx_secs[key] += d
        ctx_signal.setdefault(key, hit.signal)
        sig_secs[hit.signal] += d
        conf_sum += hit.confidence * d
        if s.get("media"):
            media_s += d
        titles[(s["app"], s.get("domain"), s.get("title"), hit.signal)] += d
        per_device[s["device"]].append((a, key))
        devices.add(s["device"])
        src_secs[s.get("source") or "unknown"] += d

    total = sum(ctx_secs.values())
    active_min = active_s / 60.0
    switches = 0
    for evs in per_device.values():
        evs.sort()
        switches += sum(1 for (_, k1), (_, k2) in zip(evs, evs[1:]) if k1 != k2)

    if total > 0:
        shares = {k: v / total for k, v in ctx_secs.items()}
        largest = max(shares, key=shares.get)
        signals = {k: round(v / total, 4) for k, v in sig_secs.items()}
    else:
        shares, largest, signals = {}, None, {}

    top_ctx = sorted(ctx_secs.items(), key=lambda kv: -kv[1])[:5]
    top_items = sorted(titles.items(), key=lambda kv: -kv[1])[:5]
    return {
        "active_s": round(active_s, 1),
        "active_minutes": round(active_min, 2),
        "idle_ratio": round(1 - active_s / window_s, 4),
        "num_contexts": sum(1 for v in shares.values() if v >= ctx_min_share),
        "largest_context": largest,
        "largest_context_share": round(shares.get(largest, 0.0), 4) if largest else 0.0,
        "switch_rate": round(switches / active_min, 3) if active_min > 0 else 0.0,
        "media_share": round(media_s / total, 4) if total else 0.0,
        "signals": signals,
        "signal_confidence": round(conf_sum / total, 3) if total else 0.0,
        "run_minutes": 0.0,
        "devices": sorted(devices),
        "source_shares": {k: round(v / total, 4) for k, v in src_secs.items()} if total else {},
        "top_contexts": [{"context": k, "minutes": round(v / 60, 1), "signal": ctx_signal[k]} for k, v in top_ctx],
        "top_items": [
            {"app": app, "app_name": pretty(app), "domain": dom, "title": title, "signal": sig, "minutes": round(v / 60, 1)}
            for (app, dom, title, sig), v in top_items
        ],
    }


def fill_run_minutes(features_by_index: dict[int, dict], min_active_s: float) -> None:
    """Adjacent windows with the same largest context form a run; each window gets the run's total minutes."""
    idxs = sorted(i for i, f in features_by_index.items() if f["active_s"] >= min_active_s and f["largest_context"])
    run: list[int] = []

    def flush():
        total = sum(features_by_index[j]["active_minutes"] for j in run)
        for j in run:
            features_by_index[j]["run_minutes"] = round(total, 2)

    for i in idxs:
        f = features_by_index[i]
        if run and i == run[-1] + 1 and features_by_index[run[-1]]["largest_context"] == f["largest_context"]:
            run.append(i)
        else:
            if run:
                flush()
            run = [i]
    if run:
        flush()
