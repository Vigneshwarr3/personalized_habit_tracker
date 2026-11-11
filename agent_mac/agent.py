"""Mac collector. Lightweight by design:

* samples every 5 s on AC power, 15 s on battery (configurable); nothing at all while locked/asleep/paused
* browser tab is read in-process (NSAppleScript) only when a browser is frontmost and the title changed / 30 s passed
* `pmset -g assertions` is only consulted after 120 s without input
* identical samples are merged locally into spans (<= 60 s) and uploaded in batches every 60 s
"""
from __future__ import annotations

import logging
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml

from .buffer import Buffer
from .idle import decide, parse_pmset_assertions

log = logging.getLogger("ht.agent")

SUPPORT_DIR = Path(os.environ.get("HT_AGENT_HOME", Path.home() / "Library/Application Support/HabitTracker"))
CONFIG_PATH = SUPPORT_DIR / "config.yaml"
PAUSE_FLAG = SUPPORT_DIR / "paused"
PRIVACY_CACHE = SUPPORT_DIR / "privacy-rules.json"

DEFAULTS = {
    "server_url": "https://your-server.your-tailnet.ts.net:8765",
    "token": "",
    "device": "macbook",
    "sample_seconds_ac": 5,
    "sample_seconds_battery": 15,
    "idle_threshold_seconds": 120,
    "upload_every_seconds": 60,
    "max_span_seconds": 60,
    "browser_refresh_seconds": 30,
    "buffer_max_mb": 500,
    "incognito": "skip",            # skip = record nothing while a private window is frontmost
    "ignore_assertion_processes": [],
}


def load_agent_config(path: Path = CONFIG_PATH) -> dict:
    cfg = dict(DEFAULTS)
    if path.exists():
        cfg.update(yaml.safe_load(path.read_text()) or {})
    return cfg


@dataclass
class Sample:
    app: str
    title: str | None
    url: str | None
    idle: bool
    media: bool

    def key(self) -> tuple:
        return (self.app, self.title, self.url, self.idle, self.media)


@dataclass
class Span:
    sample: Sample
    start: float
    end: float


@dataclass
class Spanner:
    """Merges consecutive identical samples into spans and emits heartbeats."""
    device: str
    max_span: float = 60.0
    current: Span | None = None
    out: list[dict] = field(default_factory=list)

    def _emit(self) -> None:
        s = self.current
        if s is None or s.end - s.start <= 0:
            self.current = None
            return
        self.out.append({
            "ts": round(s.start, 3), "device": self.device, "source": "mac", "app": s.sample.app,
            "title": s.sample.title, "url": s.sample.url, "idle": s.sample.idle, "media": s.sample.media,
            "interval_s": round(s.end - s.start, 3),
        })
        self.current = None

    def add(self, sample: Sample | None, now: float, step: float) -> None:
        """`sample` covers [now, now + step). None = nothing to record (locked, paused, private)."""
        cur = self.current
        if sample is None:
            self._emit()
            return
        contiguous = cur is not None and now - cur.end <= step * 1.5 + 1
        if cur is not None and (not contiguous or cur.sample.key() != sample.key() or cur.end - cur.start >= self.max_span):
            self._emit()
            cur = None
        if cur is None:
            self.current = Span(sample, now, now + step)
        else:
            cur.end = now + step

    def flush(self) -> None:
        self._emit()

    def drain(self) -> list[dict]:
        out, self.out = self.out, []
        return out


class Collector:
    def __init__(self, cfg: dict):
        from .browsers import BrowserReader

        self.cfg = cfg
        self.browsers = BrowserReader()
        self._tab_cache: tuple[str, str | None, float, object] | None = None  # (bundle, win title, ts, tab)
        self._assert_cache: tuple[float, object] | None = None
        self.ignored = set(cfg.get("ignore_assertion_processes") or [])
        from .privacy import PrivacyFilter
        self.privacy = PrivacyFilter()
        self.privacy.load_cache(PRIVACY_CACHE)

    def _assertions(self):
        from . import macos
        from .idle import DEFAULT_IGNORED

        now = time.time()
        if self._assert_cache and now - self._assert_cache[0] < 30:
            return self._assert_cache[1]
        try:
            a = parse_pmset_assertions(macos.pmset_assertions_text(), DEFAULT_IGNORED | self.ignored)
        except Exception:
            log.exception("pmset failed")
            a = None
        self._assert_cache = (now, a)
        return a

    def sample(self) -> Sample | None:
        from . import macos

        if macos.screen_locked():
            return None
        front = macos.frontmost()
        if front is None or front.app == "loginwindow":
            return None
        idle_s = macos.input_idle_seconds()
        threshold = float(self.cfg["idle_threshold_seconds"])
        state = decide(idle_s, threshold, False, self._assertions() if idle_s > threshold else None)
        if not state.send:
            return None

        title, url = front.title, None
        if self.browsers.supports(front.bundle_id):
            now = time.time()
            c = self._tab_cache
            fresh = (c and c[0] == front.bundle_id and c[1] == front.title
                     and now - c[2] < float(self.cfg["browser_refresh_seconds"]))
            tab = c[3] if fresh else None
            if not fresh:
                try:
                    tab = self.browsers.read(front.bundle_id)
                except Exception:
                    log.exception("browser read failed")
                    tab = None
                self._tab_cache = (front.bundle_id, front.title, now, tab)
            if tab is not None:
                if tab.private and self.cfg.get("incognito", "skip") == "skip":
                    return None
                url, title = tab.url, tab.title or title
        # passwords, banks, sign-in pages, ...: keep only the app name / bare site, never the title or path
        url, title = self.privacy.redact(front.app, url, title)
        return Sample(front.app, title, url, state.idle, state.media)


class Uploader:
    def __init__(self, cfg: dict, buffer: Buffer, privacy=None):
        self.cfg = cfg
        self.buffer = buffer
        self.privacy = privacy
        self.rules_fetched = 0.0
        self.backoff = 0.0
        self.next_try = 0.0
        self.server_paused = False
        self.client = httpx.Client(
            base_url=cfg["server_url"].rstrip("/"), timeout=20,
            headers={"Authorization": f"Bearer {cfg['token']}", "User-Agent": "habit-tracker-mac/1.0"},
        )

    def upload(self) -> None:
        if time.time() < self.next_try:
            return
        try:
            sent_any = False
            while True:
                nb = self.buffer.next_batch()
                if nb is None:
                    break
                bid, items = nb
                r = self.client.post("/heartbeats", json={"batch_id": bid, "heartbeats": items})
                if r.status_code in (401, 403):
                    log.error("server rejected the token (%s); check config.yaml", r.status_code)
                    raise RuntimeError("auth")
                r.raise_for_status()
                self.buffer.ack(bid)
                self.server_paused = bool(r.json().get("paused"))
                sent_any = True
            if not sent_any:
                r = self.client.get("/status")
                r.raise_for_status()
                self.server_paused = bool(r.json().get("paused"))
            if self.privacy is not None and time.time() - self.rules_fetched > 1800:
                r = self.client.get("/privacy-rules")
                if r.status_code == 200:
                    self.privacy.set_rules(r.json())
                    self.privacy.save_cache(PRIVACY_CACHE)
                    self.rules_fetched = time.time()
            self.backoff = 0.0
        except Exception as e:  # offline, server down, ... keep data, retry later
            self.backoff = min(900.0, max(30.0, self.backoff * 2))
            self.next_try = time.time() + self.backoff
            log.info("upload failed (%s); %d heartbeats buffered; retry in %.0fs", e, self.buffer.count(), self.backoff)


def run(cfg: dict | None = None) -> None:
    from . import macos

    cfg = cfg or load_agent_config()
    if not cfg.get("token"):
        raise SystemExit(f"no token configured in {CONFIG_PATH}")
    buffer = Buffer(SUPPORT_DIR / "buffer.db", max_bytes=int(cfg["buffer_max_mb"]) * 1024 * 1024)
    collector = Collector(cfg)
    uploader = Uploader(cfg, buffer, collector.privacy)
    spanner = Spanner(cfg["device"], float(cfg["max_span_seconds"]))
    stop = {"flag": False}

    def _stop(*_):
        stop["flag"] = True
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    last_upload = 0.0
    last_power_check = 0.0
    battery = False
    warned_full = False
    log.info("agent started: device=%s server=%s", cfg["device"], cfg["server_url"])
    try:
        if not macos.ensure_screen_title_access():
            log.warning("no Screen Recording permission: window titles of apps are not recorded. Enable 'Python' in "
                        "System Settings > Privacy & Security > Screen & System Audio Recording, then restart the agent")
    except Exception:
        log.exception("screen-recording permission check failed")
    while not stop["flag"]:
        now = time.time()
        if now - last_power_check > 300:
            battery = macos.on_battery()
            last_power_check = now
        step = float(cfg["sample_seconds_battery"] if battery else cfg["sample_seconds_ac"])

        paused = PAUSE_FLAG.exists() or uploader.server_paused
        if paused:
            sample = None
        elif buffer.full():
            if not warned_full:
                log.error("offline buffer is full (%s MB); collection stopped until the server is reachable",
                          cfg["buffer_max_mb"])
                warned_full = True
            sample = None
        else:
            warned_full = False
            try:
                sample = collector.sample()
            except Exception:
                log.exception("sampling failed")
                sample = None
        spanner.add(sample, now, step)
        for hb in spanner.drain():
            buffer.add(hb)

        if now - last_upload >= float(cfg["upload_every_seconds"]):
            spanner.flush()
            for hb in spanner.drain():
                buffer.add(hb)
            uploader.upload()
            last_upload = now

        time.sleep(max(1.0, step if not paused else 30.0))

    spanner.flush()
    for hb in spanner.drain():
        buffer.add(hb)
    uploader.upload()
    log.info("agent stopped")
