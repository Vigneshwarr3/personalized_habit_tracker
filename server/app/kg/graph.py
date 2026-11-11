"""Knowledge graph for items the rules don't know. Stored in SQLite (kg_nodes / kg_edges).

Lookup order for an item:  (a) exact node  ->  (b) parent (channel, then site path, then domain; only if the
parent is consistently classified)  ->  (c) similar title (embedding cosine >= threshold)  ->  (d) LLM.
(a)-(c) run instantly when a heartbeat arrives; misses go to a queue that a background worker drains
(it first looks up the YouTube channel, retries (b)/(c), and only then calls the LLM, within budget).
Every outcome is logged in kg_lookups. Nothing is deleted: edits deactivate the old edge and add a new one.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Callable
from zoneinfo import ZoneInfo

import httpx
import numpy as np

from ..classify.rules import SignalHit
from ..config import REPO_ROOT, Config
from ..privacy import Privacy
from .embed import FastEmbedder, HashEmbedder, from_blob, to_blob
from .items import Item, item_from_segment
from .llm import LLMError, Provider, build_payload, provider_from_config, validate_answer

log = logging.getLogger("ht.kg")

SCHEMA = """
CREATE TABLE IF NOT EXISTS kg_nodes (
    id         INTEGER PRIMARY KEY,
    type       TEXT NOT NULL,           -- video | page | title | app | channel | path | domain
    key        TEXT NOT NULL,
    label      TEXT,
    domain     TEXT,
    embedding  BLOB,
    meta       TEXT,
    created_at REAL NOT NULL,
    last_seen  REAL NOT NULL,
    seen_count INTEGER NOT NULL DEFAULT 1,
    UNIQUE(type, key)
);
CREATE TABLE IF NOT EXISTS kg_edges (
    id             INTEGER PRIMARY KEY,
    src_id         INTEGER NOT NULL REFERENCES kg_nodes(id),
    rel            TEXT NOT NULL,       -- belongs_to | classified_as
    dst_id         INTEGER REFERENCES kg_nodes(id),   -- belongs_to target
    signal         TEXT,                -- classified_as target
    confidence     REAL,
    source         TEXT,                -- llm | semantic | user
    reason         TEXT,
    first_seen     REAL NOT NULL,
    last_seen      REAL NOT NULL,
    evidence_count INTEGER NOT NULL DEFAULT 1,
    needs_review   INTEGER NOT NULL DEFAULT 0,
    active         INTEGER NOT NULL DEFAULT 1,
    deactivated_at REAL,
    deactivated_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_kg_edges_src ON kg_edges(src_id, rel, active);
CREATE TABLE IF NOT EXISTS kg_lookups (
    id         INTEGER PRIMARY KEY,
    ts         REAL NOT NULL,
    node_id    INTEGER,
    item_key   TEXT,
    outcome    TEXT NOT NULL,           -- exact | parent | semantic | llm | queued
    signal     TEXT,
    confidence REAL,
    detail     TEXT
);
CREATE INDEX IF NOT EXISTS idx_kg_lookups_ts ON kg_lookups(ts);
CREATE TABLE IF NOT EXISTS kg_queue (
    id            INTEGER PRIMARY KEY,
    node_id       INTEGER NOT NULL UNIQUE REFERENCES kg_nodes(id),
    status        TEXT NOT NULL,        -- pending | done | failed
    attempts      INTEGER NOT NULL DEFAULT 0,
    queued_logged INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id         INTEGER PRIMARY KEY,
    ts         REAL NOT NULL,
    provider   TEXT,
    model      TEXT,
    node_id    INTEGER,
    ok         INTEGER NOT NULL,
    error      TEXT,
    signal     TEXT,
    confidence REAL,
    latency_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_ts ON llm_calls(ts);
"""

PARENT_ORDER = {"channel": 0, "path": 1, "domain": 2}
EXACT_TYPES = ("video", "page", "title", "search", "app")
_DEFAULT = object()


@dataclass
class Eff:
    signal: str
    confidence: float
    source: str
    edge_id: int
    needs_review: bool


def youtube_oembed_channel(video_id: str) -> str | None:
    """Channel name for a public video. Sends only the video id to youtube.com (which you already visited)."""
    r = httpx.get("https://www.youtube.com/oembed",
                  params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"}, timeout=5)
    if r.status_code != 200:
        return None
    return (r.json().get("author_name") or "").strip() or None


class KnowledgeGraph:
    def __init__(self, cfg: Config, provider=_DEFAULT, embedder=_DEFAULT, channel_fetcher=_DEFAULT,
                 clock: Callable[[], float] = time.time):
        k = cfg.settings.get("knowledge_graph", {}) or {}
        self.cfg = cfg
        self.enabled = bool(k.get("enabled", True))
        self.min_rule_conf = float(k.get("min_rule_confidence", 0.6))
        self.sem_threshold = float(k.get("semantic_threshold", 0.80))
        self.parent_min_ev = int(k.get("parent_min_evidence", 2))
        self.parent_min_agree = float(k.get("parent_min_agreement", 0.8))
        self.review_below = float(k.get("review_below", 0.6))
        self.llm_cfg = k.get("llm", {}) or {}
        self.allowed = [s for s in cfg.signals if s != "unknown"]
        self.descriptions = cfg.settings.get("signal_descriptions", {}) or {}
        self.browsers = cfg.browsers
        self.platforms = {d.lower() for d in k.get("platform_domains", ["youtube.com"])}
        self.search_patterns = list(k.get("local_only_pages", []))
        self.privacy = Privacy.from_settings(cfg.settings)
        self.clock = clock

        self.provider: Provider | None = provider_from_config(self.llm_cfg) if provider is _DEFAULT else provider
        if embedder is _DEFAULT:
            if k.get("embedding_backend", "fastembed") == "hash":
                embedder = HashEmbedder()
            else:
                cache = k.get("model_cache_dir", "data/models")
                cache_path = REPO_ROOT / cache if not cache.startswith("/") else __import__("pathlib").Path(cache)
                embedder = FastEmbedder(k.get("embedding_model", "sentence-transformers/all-MiniLM-L6-v2"), cache_path)
        self.embedder = embedder
        if channel_fetcher is _DEFAULT:
            channel_fetcher = youtube_oembed_channel if k.get("youtube_channel_lookup", True) else None
        self.channel_fetcher = channel_fetcher

        self._lock = threading.RLock()
        self._version = 0
        self._sem_cache: tuple[int, list, np.ndarray | None] | None = None

    # ------------------------------------------------------------------ schema
    @staticmethod
    def init_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ nodes / edges
    def _node(self, conn, type_: str, key: str):
        return conn.execute("SELECT * FROM kg_nodes WHERE type=? AND key=?", (type_, key)).fetchone()

    def _upsert_node(self, conn, type_: str, key: str, label: str | None, domain: str | None = None) -> int:
        now = self.clock()
        row = self._node(conn, type_, key)
        if row:
            conn.execute("UPDATE kg_nodes SET last_seen=?, seen_count=seen_count+1, label=COALESCE(?, label) "
                         "WHERE id=?", (now, label, row["id"]))
            return row["id"]
        cur = conn.execute("INSERT INTO kg_nodes(type, key, label, domain, created_at, last_seen) VALUES(?,?,?,?,?,?)",
                           (type_, key, label, domain, now, now))
        return cur.lastrowid

    def _link(self, conn, src: int, dst: int) -> None:
        if src == dst:
            return
        hit = conn.execute("SELECT 1 FROM kg_edges WHERE src_id=? AND dst_id=? AND rel='belongs_to' AND active=1",
                           (src, dst)).fetchone()
        if not hit:
            now = self.clock()
            conn.execute("INSERT INTO kg_edges(src_id, rel, dst_id, first_seen, last_seen) VALUES(?, 'belongs_to', ?, ?, ?)",
                         (src, dst, now, now))

    def _parents(self, conn, node_id: int) -> list[sqlite3.Row]:
        seen, frontier, out = {node_id}, [node_id], []
        for _ in range(3):
            nxt = []
            for nid in frontier:
                for r in conn.execute("SELECT n.* FROM kg_edges e JOIN kg_nodes n ON n.id=e.dst_id "
                                      "WHERE e.src_id=? AND e.rel='belongs_to' AND e.active=1", (nid,)):
                    if r["id"] not in seen:
                        seen.add(r["id"])
                        out.append(r)
                        nxt.append(r["id"])
            frontier = nxt
        return sorted(out, key=lambda r: PARENT_ORDER.get(r["type"], 9))

    def _class_edges(self, conn, node_id: int) -> list[sqlite3.Row]:
        return conn.execute("SELECT * FROM kg_edges WHERE src_id=? AND rel='classified_as' AND active=1 "
                            "ORDER BY last_seen DESC, id DESC", (node_id,)).fetchall()

    def effective(self, conn, node_id: int, as_parent: bool = False) -> Eff | None:
        edges = self._class_edges(conn, node_id)
        if not edges:
            return None
        users = [e for e in edges if e["source"] == "user"]
        if users:
            e = users[0]
            return Eff(e["signal"], 1.0, "user", e["id"], False)
        if not as_parent:
            e = edges[0]
            return Eff(e["signal"], float(e["confidence"] or 0), e["source"], e["id"], bool(e["needs_review"]))
        # parent: only inherit when consistently classified
        counts: dict[str, int] = {}
        conf: dict[str, float] = {}
        best_edge: dict[str, sqlite3.Row] = {}
        for e in edges:
            counts[e["signal"]] = counts.get(e["signal"], 0) + int(e["evidence_count"])
            conf[e["signal"]] = max(conf.get(e["signal"], 0.0), float(e["confidence"] or 0))
            best_edge.setdefault(e["signal"], e)
        total = sum(counts.values())
        sig = max(counts, key=counts.get)
        if counts[sig] >= self.parent_min_ev and counts[sig] / total >= self.parent_min_agree:
            return Eff(sig, conf[sig], "parent", best_edge[sig]["id"], False)
        return None

    def _write_class(self, conn, node_id: int, signal: str, conf: float, source: str, reason: str) -> int:
        now = self.clock()
        cur = conn.execute(
            "INSERT INTO kg_edges(src_id, rel, signal, confidence, source, reason, first_seen, last_seen, needs_review)"
            " VALUES(?, 'classified_as', ?, ?, ?, ?, ?, ?, ?)",
            (node_id, signal, conf, source, reason, now, now, int(source != "user" and conf < self.review_below)),
        )
        self._bump()
        return cur.lastrowid

    def _bump_parent(self, conn, parent_id: int, signal: str, conf: float) -> None:
        now = self.clock()
        e = conn.execute("SELECT * FROM kg_edges WHERE src_id=? AND rel='classified_as' AND active=1 AND source='llm' "
                         "AND signal=?", (parent_id, signal)).fetchone()
        if e:
            n = int(e["evidence_count"])
            conn.execute("UPDATE kg_edges SET evidence_count=?, confidence=?, last_seen=? WHERE id=?",
                         (n + 1, (float(e["confidence"] or 0) * n + conf) / (n + 1), now, e["id"]))
        else:
            conn.execute("INSERT INTO kg_edges(src_id, rel, signal, confidence, source, reason, first_seen, last_seen)"
                         " VALUES(?, 'classified_as', ?, ?, 'llm', 'aggregated from its items', ?, ?)",
                         (parent_id, signal, conf, now, now))
        self._bump()

    def _deactivate_class(self, conn, node_id: int, reason: str, only_user: bool = False) -> None:
        q = "UPDATE kg_edges SET active=0, deactivated_at=?, deactivated_reason=? WHERE src_id=? AND rel='classified_as' AND active=1"
        if only_user:
            q += " AND source='user'"
        conn.execute(q, (self.clock(), reason, node_id))
        self._bump()

    def _bump(self) -> None:
        with self._lock:
            self._version += 1

    def _log(self, conn, node_id, item_key, outcome, eff: Eff | None = None, detail: str | None = None) -> None:
        conn.execute("INSERT INTO kg_lookups(ts, node_id, item_key, outcome, signal, confidence, detail) "
                     "VALUES(?,?,?,?,?,?,?)",
                     (self.clock(), node_id, item_key, outcome, eff.signal if eff else None,
                      eff.confidence if eff else None, detail))

    # ------------------------------------------------------------------ semantic
    def _embed_one(self, text: str) -> np.ndarray | None:
        if self.embedder is None:
            return None
        try:
            return self.embedder.embed([text])[0]
        except Exception:
            log.exception("embedding failed; semantic stage skipped")
            return None

    def _semantic_candidates(self, conn) -> tuple[list[dict], np.ndarray | None]:
        with self._lock:
            if self._sem_cache and self._sem_cache[0] == self._version:
                return self._sem_cache[1], self._sem_cache[2]
            version = self._version
        rows = conn.execute(
            "SELECT n.id, n.label, n.embedding, e.signal, e.confidence, e.source, e.last_seen FROM kg_nodes n "
            "JOIN kg_edges e ON e.src_id=n.id AND e.rel='classified_as' AND e.active=1 "
            "WHERE n.embedding IS NOT NULL AND e.source IN ('llm','user') ORDER BY e.last_seen").fetchall()
        best: dict[int, dict] = {}
        for r in rows:  # user edges win, else latest
            cur = best.get(r["id"])
            if cur is None or r["source"] == "user" or cur["source"] != "user":
                best[r["id"]] = dict(r)
        meta = [b for b in best.values() if b["source"] == "user" or float(b["confidence"] or 0) >= self.review_below]
        mat = np.vstack([from_blob(m["embedding"]) for m in meta]) if meta else None
        with self._lock:
            self._sem_cache = (version, meta, mat)
        return meta, mat

    def _semantic(self, conn, node) -> tuple[Eff, str] | None:
        if node["embedding"] is None:
            return None
        meta, mat = self._semantic_candidates(conn)
        if mat is None:
            return None
        sims = mat @ from_blob(node["embedding"])
        order = np.argsort(-sims)
        for i in order[:5]:
            m = meta[int(i)]
            if m["id"] == node["id"]:
                continue
            if float(sims[i]) >= self.sem_threshold:
                conf = round(min(float(m["confidence"] or 1.0), float(sims[i])), 3)
                return Eff(m["signal"], conf, "semantic", 0, conf < self.review_below), \
                    f"similar ({float(sims[i]):.2f}) to: {m['label']}"
            break
        return None

    # ------------------------------------------------------------------ lookup
    def _inheritable_parents(self, conn, node_id: int) -> list[sqlite3.Row]:
        """Parents whose classification new items may inherit. Platform domains (youtube.com, medium.com, ...)
        host every kind of content, so only their channels / sub-paths are used, never the platform itself."""
        return [p for p in self._parents(conn, node_id)
                if not (p["type"] == "domain" and p["key"] in self.platforms)]

    def _channel_pending(self, node) -> bool:
        """A video whose channel we haven't looked up yet: the channel check must come before the title check."""
        return (node["type"] == "video" and self.channel_fetcher is not None
                and not json.loads(node["meta"] or "{}").get("channel_checked"))

    def _lookup_local(self, conn, node, allow_semantic: bool = True) -> tuple[str, Eff, str | None] | None:
        e = self.effective(conn, node["id"])
        if e:
            return "exact", e, None
        for p in self._inheritable_parents(conn, node["id"]):
            e = self.effective(conn, p["id"], as_parent=True)
            if e:
                return "parent", e, f"{p['type']}: {p['label']}"
        if not allow_semantic:
            return None
        s = self._semantic(conn, node)
        if s:
            return "semantic", s[0], s[1]
        return None

    def ensure_item(self, conn, item: Item) -> int:
        nid = self._upsert_node(conn, item.type, item.key, item.label, item.domain)
        if item.local_only:
            conn.execute("UPDATE kg_nodes SET meta=json_set(COALESCE(meta,'{}'), '$.local_only', json('true')) "
                         "WHERE id=?", (nid,))
        prev = nid
        for ptype, pkey, plabel in item.parents:
            pid = self._upsert_node(conn, ptype, pkey, plabel, item.domain)
            self._link(conn, prev, pid)
            prev = pid
        if item.title:
            row = conn.execute("SELECT embedding FROM kg_nodes WHERE id=?", (nid,)).fetchone()
            if row["embedding"] is None:
                v = self._embed_one(item.title)
                if v is not None:
                    conn.execute("UPDATE kg_nodes SET embedding=? WHERE id=?", (to_blob(v), nid))
        return nid

    def observe(self, conn, seg: dict) -> str | None:
        """Called for each NEW activity span the rules don't know. Returns the lookup outcome."""
        if not self.enabled:
            return None
        if self.privacy.is_private(seg.get("app"), seg.get("domain"), seg.get("title")):
            return None  # sensitive: never stored in the graph, never sent to the LLM
        item = item_from_segment(seg, self.browsers, self.search_patterns)
        if item is None:
            return None
        nid = self.ensure_item(conn, item)
        node = conn.execute("SELECT * FROM kg_nodes WHERE id=?", (nid,)).fetchone()
        res = self._lookup_local(conn, node, allow_semantic=not self._channel_pending(node))
        if res:
            outcome, eff, detail = res
            if outcome == "semantic":
                self._write_class(conn, nid, eff.signal, eff.confidence, "semantic", detail or "")
            self._log(conn, nid, f"{item.type}:{item.key}", outcome, eff, detail)
            return outcome
        self._enqueue(conn, nid)
        return "pending"

    def _enqueue(self, conn, nid: int) -> None:
        now = self.clock()
        conn.execute("INSERT INTO kg_queue(node_id, status, created_at, updated_at) VALUES(?, 'pending', ?, ?) "
                     "ON CONFLICT(node_id) DO UPDATE SET status='pending', updated_at=excluded.updated_at "
                     "WHERE kg_queue.status='done'", (nid, now, now))

    # ------------------------------------------------------------------ read-time resolver
    def resolver(self, conn) -> Callable[[dict, SignalHit], SignalHit]:
        cache: dict[tuple, Eff | None] = {}

        def resolve(seg: dict, hit: SignalHit) -> SignalHit:
            if not self.enabled or self.privacy.is_private(seg.get("app"), seg.get("domain"), seg.get("title")):
                return hit
            item = item_from_segment(seg, self.browsers, self.search_patterns)
            if item is None:
                return hit
            k = (item.type, item.key)
            if k not in cache:
                node = self._node(conn, item.type, item.key)
                eff = None
                if node is not None:
                    eff = self.effective(conn, node["id"])
                    if eff is None:
                        for p in self._inheritable_parents(conn, node["id"]):
                            eff = self.effective(conn, p["id"], as_parent=True)
                            if eff:
                                break
                cache[k] = eff
            eff = cache[k]
            if eff is not None and eff.source == "user":
                return SignalHit(eff.signal, 1.0, "kg-user")
            if hit.confidence >= self.min_rule_conf:
                return hit
            if eff is not None:
                return SignalHit(eff.signal, eff.confidence, f"kg-{eff.source}")
            return hit

        return resolve

    # ------------------------------------------------------------------ LLM worker
    def _day_start(self) -> float:
        tz = ZoneInfo(self.cfg.timezone)
        d = datetime.fromtimestamp(self.clock(), tz).date()
        return datetime.combine(d, dtime(0), tz).timestamp()

    def calls_today(self, conn) -> int:
        return conn.execute("SELECT COUNT(*) FROM llm_calls WHERE ts >= ?", (self._day_start(),)).fetchone()[0]

    def _gate(self, conn) -> str | None:
        if not self.enabled:
            return "disabled"
        if self.llm_cfg.get("dry_run"):
            return "dry-run"
        if self.provider is None:
            return "no-api-key"
        if self.calls_today(conn) >= int(self.llm_cfg.get("daily_budget", 200)):
            return "daily-budget"
        recent = conn.execute("SELECT COUNT(*) FROM llm_calls WHERE ts >= ?", (self.clock() - 60,)).fetchone()[0]
        if recent >= int(self.llm_cfg.get("max_per_minute", 20)):
            return "rate-limit"
        return None

    def _channel_of(self, conn, node_id: int) -> sqlite3.Row | None:
        return conn.execute("SELECT n.* FROM kg_edges e JOIN kg_nodes n ON n.id=e.dst_id WHERE e.src_id=? "
                            "AND e.rel='belongs_to' AND e.active=1 AND n.type='channel'", (node_id,)).fetchone()

    def _ensure_channel(self, conn, node) -> None:
        if node["type"] != "video" or self.channel_fetcher is None:
            return
        meta = json.loads(node["meta"] or "{}")
        if meta.get("channel_checked") or self._channel_of(conn, node["id"]):
            return
        try:
            name = self.channel_fetcher(node["key"])
        except Exception as e:  # offline etc. -> try again next round
            log.info("channel lookup failed for a video: %s", e.__class__.__name__)
            return
        meta["channel_checked"] = True
        conn.execute("UPDATE kg_nodes SET meta=? WHERE id=?", (json.dumps(meta), node["id"]))
        if name:
            cid = self._upsert_node(conn, "channel", f"youtube:{name.lower()}", name, "youtube.com")
            self._link(conn, node["id"], cid)
            yt = self._upsert_node(conn, "domain", "youtube.com", "youtube.com", "youtube.com")
            self._link(conn, cid, yt)

    def payload_for(self, conn, node) -> dict:
        if node["type"] == "search" or json.loads(node["meta"] or "{}").get("local_only"):
            return {}  # belt and braces: search words never leave the server
        ch = self._channel_of(conn, node["id"])
        if node["type"] == "app":
            return build_payload(None, None, None, node["label"])
        title = node["label"] if node["type"] in ("video", "page", "title") and node["label"] != node["key"] else None
        return build_payload(title, node["domain"], ch["label"] if ch else None, None)

    def process_queue(self, conn, limit: int = 25) -> dict:
        stats = {"exact": 0, "parent": 0, "semantic": 0, "llm": 0, "queued": 0, "failed": 0}
        rows = conn.execute("SELECT q.*, n.type, n.key FROM kg_queue q JOIN kg_nodes n ON n.id=q.node_id "
                            "WHERE q.status='pending' ORDER BY q.updated_at, q.id LIMIT ?", (limit,)).fetchall()
        for q in rows:
            node = conn.execute("SELECT * FROM kg_nodes WHERE id=?", (q["node_id"],)).fetchone()
            item_key = f"{node['type']}:{node['key']}"
            self._ensure_channel(conn, node)
            res = self._lookup_local(conn, node)
            if res:
                outcome, eff, detail = res
                if outcome == "semantic":
                    self._write_class(conn, node["id"], eff.signal, eff.confidence, "semantic", detail or "")
                self._log(conn, node["id"], item_key, outcome, eff, detail)
                self._set_queue(conn, q["id"], "done")
                stats[outcome] += 1
                continue
            if json.loads(node["meta"] or "{}").get("local_only"):
                # search pages etc.: the title is your search words -> never sent; stays unknown until you label it
                self._log(conn, node["id"], item_key, "local-only", None, "never sent to the LLM")
                self._set_queue(conn, q["id"], "local_only")
                stats["local_only"] = stats.get("local_only", 0) + 1
                continue
            gate = self._gate(conn)
            if gate:  # can't ask the LLM now: stays pending (rotates to the back of the queue), logged once
                if not q["queued_logged"]:
                    self._log(conn, node["id"], item_key, "queued", None, gate)
                conn.execute("UPDATE kg_queue SET queued_logged=1, last_error=?, updated_at=? WHERE id=?",
                             (gate, self.clock(), q["id"]))
                stats["queued"] += 1
                continue
            payload = self.payload_for(conn, node)
            if not payload:
                self._set_queue(conn, q["id"], "failed", "nothing safe to send")
                stats["failed"] += 1
                continue
            t0 = time.time()
            try:
                ans = validate_answer(self.provider.classify(payload, self.allowed, self.descriptions), self.allowed)
            except LLMError as e:
                conn.execute("INSERT INTO llm_calls(ts, provider, model, node_id, ok, error, latency_ms) "
                             "VALUES(?,?,?,?,0,?,?)", (self.clock(), self.provider.name, self.provider.model,
                                                       node["id"], str(e)[:300], int((time.time() - t0) * 1000)))
                attempts = q["attempts"] + 1
                status = "failed" if attempts >= 3 else "pending"
                conn.execute("UPDATE kg_queue SET attempts=?, status=?, last_error=?, updated_at=? WHERE id=?",
                             (attempts, status, str(e)[:300], self.clock(), q["id"]))
                stats["failed"] += 1
                if "429" in str(e) or "network" in str(e):
                    break
                continue
            conn.execute("INSERT INTO llm_calls(ts, provider, model, node_id, ok, signal, confidence, latency_ms) "
                         "VALUES(?,?,?,?,1,?,?,?)", (self.clock(), self.provider.name, self.provider.model, node["id"],
                                                     ans.signal, ans.confidence, int((time.time() - t0) * 1000)))
            self._write_class(conn, node["id"], ans.signal, ans.confidence, "llm", ans.reason)
            for p in self._parents(conn, node["id"]):
                if p["type"] in ("channel", "path", "domain"):
                    self._bump_parent(conn, p["id"], ans.signal, ans.confidence)
            self._log(conn, node["id"], item_key, "llm", Eff(ans.signal, ans.confidence, "llm", 0, False), ans.reason)
            self._set_queue(conn, q["id"], "done")
            stats["llm"] += 1
        return stats

    def retire_collapsed_search_pages(self, conn) -> int:
        """One-time fix: before searches were tracked per title, all searches of an engine shared one page node
        (query strings are stripped). Switch off classifications on those shared nodes (kept in history)."""
        from .items import matches_page_pattern
        n = 0
        for node in conn.execute("SELECT id, key, domain FROM kg_nodes WHERE type='page'").fetchall():
            path = "/" + node["key"].split("/", 1)[1] if "/" in node["key"] else "/"
            if node["domain"] and matches_page_pattern(node["domain"], path, self.search_patterns):
                cur = conn.execute("UPDATE kg_edges SET active=0, deactivated_at=?, deactivated_reason=? WHERE src_id=? "
                                   "AND rel='classified_as' AND active=1",
                                   (self.clock(), "search pages are now classified per search, locally", node["id"]))
                n += cur.rowcount
        if n:
            self._bump()
        return n

    def _set_queue(self, conn, qid: int, status: str, err: str | None = None) -> None:
        conn.execute("UPDATE kg_queue SET status=?, last_error=COALESCE(?, last_error), updated_at=? WHERE id=?",
                     (status, err, self.clock(), qid))

    def retry(self, conn, node_id: int) -> None:
        conn.execute("UPDATE kg_queue SET status='pending', attempts=0, queued_logged=0, updated_at=? WHERE node_id=?",
                     (self.clock(), node_id))

    # ------------------------------------------------------------------ user edits
    def correct(self, conn, node_id: int, signal: str, apply_to_parent: bool = False) -> dict:
        if signal not in self.allowed:
            raise ValueError(f"unknown signal {signal!r}")
        node = conn.execute("SELECT * FROM kg_nodes WHERE id=?", (node_id,)).fetchone()
        if node is None:
            raise KeyError(node_id)
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._deactivate_class(conn, node_id, "replaced by your correction")
            self._write_class(conn, node_id, signal, 1.0, "user", "your correction")
            conn.execute("UPDATE kg_queue SET status='done', updated_at=? WHERE node_id=?", (self.clock(), node_id))
            parent = None
            if apply_to_parent:
                parent = self._channel_of(conn, node_id) if node["type"] == "video" else None
                if parent is None and node["type"] != "video":
                    parent = next((p for p in self._parents(conn, node_id) if p["type"] == "domain"), None)
                if parent is not None:
                    self._deactivate_class(conn, parent["id"], "replaced by your correction", only_user=True)
                    self._write_class(conn, parent["id"], signal, 1.0, "user", "your correction (whole channel/site)")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return {"node_id": node_id, "signal": signal,
                "parent": {"id": parent["id"], "type": parent["type"], "label": parent["label"]} if parent else None}

    def deactivate_edge(self, conn, edge_id: int) -> bool:
        cur = conn.execute("UPDATE kg_edges SET active=0, deactivated_at=?, deactivated_reason='removed in dashboard' "
                           "WHERE id=? AND rel='classified_as' AND active=1", (self.clock(), edge_id))
        self._bump()
        return cur.rowcount > 0

    # ------------------------------------------------------------------ backfill
    def backfill(self, conn, rule_match: Callable[[dict], SignalHit]) -> int:
        n = 0
        for r in conn.execute("SELECT app, url, domain, MAX(title) AS title FROM heartbeats "
                              "GROUP BY app, url, domain, title").fetchall():
            seg = dict(r)
            if rule_match(seg).confidence < self.min_rule_conf:
                self.observe(conn, seg)
                n += 1
        return n

    # ------------------------------------------------------------------ dashboard queries
    def stats(self, conn, since: float | None = None) -> dict:
        nodes = {r[0]: r[1] for r in conn.execute("SELECT type, COUNT(*) FROM kg_nodes GROUP BY type")}
        edges = {r[0]: r[1] for r in conn.execute("SELECT rel, COUNT(*) FROM kg_edges WHERE active=1 GROUP BY rel")}
        by_source = {r[0]: r[1] for r in conn.execute(
            "SELECT source, COUNT(*) FROM kg_edges WHERE active=1 AND rel='classified_as' GROUP BY source")}
        q = "SELECT outcome, COUNT(*) FROM kg_lookups"
        args: tuple = ()
        if since:
            q += " WHERE ts >= ?"
            args = (since,)
        outcomes = {r[0]: r[1] for r in conn.execute(q + " GROUP BY outcome", args)}
        local = sum(outcomes.get(k, 0) for k in ("exact", "parent", "semantic"))
        llm = outcomes.get("llm", 0)
        queue = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM kg_queue GROUP BY status")}
        calls = conn.execute("SELECT COUNT(*), SUM(ok) FROM llm_calls").fetchone()
        return {
            "nodes": nodes, "edges": edges, "classified_by_source": by_source,
            "lookups": outcomes, "llm_calls_saved": local, "llm_answers": llm,
            "cache_hit_rate": round(local / (local + llm), 3) if (local + llm) else None,
            "queue": queue, "llm_calls_total": calls[0], "llm_calls_ok": calls[1] or 0,
            "llm_calls_today": self.calls_today(conn), "daily_budget": int(self.llm_cfg.get("daily_budget", 200)),
            "dry_run": bool(self.llm_cfg.get("dry_run")), "provider": getattr(self.provider, "model", None),
            "llm_ready": self.provider is not None, "embedder": getattr(self.embedder, "name", None),
        }

    def _describe(self, conn, node) -> dict:
        eff = self.effective(conn, node["id"])
        ch = self._channel_of(conn, node["id"])
        e = conn.execute("SELECT * FROM kg_edges WHERE id=?", (eff.edge_id,)).fetchone() if eff and eff.edge_id else None
        q = conn.execute("SELECT status, last_error FROM kg_queue WHERE node_id=?", (node["id"],)).fetchone()
        return {
            "node_id": node["id"], "type": node["type"], "key": node["key"], "label": node["label"],
            "domain": node["domain"], "channel": ch["label"] if ch else None, "seen_count": node["seen_count"],
            "signal": eff.signal if eff else None, "confidence": eff.confidence if eff else None,
            "source": eff.source if eff else None, "edge_id": eff.edge_id if eff else None,
            "reason": e["reason"] if e else None, "needs_review": bool(e["needs_review"]) if e else False,
            "queue_status": q["status"] if q else None, "queue_error": q["last_error"] if q else None,
            "last_seen": node["last_seen"],
        }

    def review_queue(self, conn, limit: int = 100) -> list[dict]:
        ids = [r[0] for r in conn.execute(
            "SELECT DISTINCT src_id FROM kg_edges WHERE rel='classified_as' AND active=1 AND needs_review=1 "
            "UNION SELECT node_id FROM kg_queue WHERE status IN ('pending','failed') LIMIT ?", (limit,))]
        out = []
        for nid in ids:
            node = conn.execute("SELECT * FROM kg_nodes WHERE id=?", (nid,)).fetchone()
            d = self._describe(conn, node)
            if d["source"] == "user":
                continue
            out.append(d)
        return sorted(out, key=lambda d: -d["last_seen"])

    def browse(self, conn, q: str | None = None, type_: str | None = None, limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM kg_nodes WHERE 1=1"
        args: list = []
        if type_:
            sql += " AND type=?"
            args.append(type_)
        if q:
            sql += " AND (label LIKE ? OR key LIKE ? OR domain LIKE ?)"
            args += [f"%{q}%"] * 3
        sql += " ORDER BY last_seen DESC LIMIT ?"
        args.append(limit)
        return [self._describe(conn, n) for n in conn.execute(sql, args).fetchall()]

    def recent_lookups(self, conn, limit: int = 100) -> list[dict]:
        return [dict(r) for r in conn.execute(
            "SELECT l.*, n.label, n.type FROM kg_lookups l LEFT JOIN kg_nodes n ON n.id=l.node_id "
            "ORDER BY l.id DESC LIMIT ?", (limit,))]
