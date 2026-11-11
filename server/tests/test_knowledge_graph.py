"""Phase 2: knowledge graph lookup order, LLM privacy, budget/dry-run/no-key, corrections, review queue."""
from __future__ import annotations

import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from server.app.kg.embed import HashEmbedder
from server.app.kg.graph import KnowledgeGraph
from server.app.kg.llm import OpenAICompatibleProvider, build_payload, validate_answer, LLMError
from server.app.main import create_app

from .conftest import ADMIN, DAY, local_ts, post, span

SAFE_KEYS = {"title", "site", "channel", "app"}


class FakeLLM:
    """Records every payload; answers from a lookup table keyed by title words."""
    name = "fake"
    model = "fake-1"

    def __init__(self, answers: dict[str, tuple[str, float]] | None = None, raw=None):
        self.calls: list[dict] = []
        self.answers = answers or {}
        self.raw = raw

    def classify(self, payload, allowed, descriptions):
        self.calls.append(payload)
        if self.raw is not None:
            return self.raw
        text = " ".join(str(v) for v in payload.values()).lower()
        for word, (sig, conf) in self.answers.items():
            if word in text:
                return {"signal": sig, "confidence": conf, "reason": f"matched {word}"}
        return {"signal": "other", "confidence": 0.3, "reason": "no idea"}


CHANNELS = {"vid0000001": "MIT OpenCourseWare", "vid0000002": "MIT OpenCourseWare",
            "vid0000003": "MIT OpenCourseWare", "vid0000004": "Random Uploads", "vid0000005": "Gaming Central"}


def make_client(cfg, llm=None, **kw):
    graph = KnowledgeGraph(cfg, provider=llm, embedder=HashEmbedder(),
                           channel_fetcher=lambda vid: CHANNELS.get(vid), **kw)
    app = create_app(cfg, start_background=False, kg=graph)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {ADMIN}"
    return c, graph


def yt(start, vid, title, minutes=5):
    # 'Vlog' style titles with no education keywords -> rules give entertainment @0.5 -> goes to the KG
    return span(start, minutes, "Google Chrome", f"{title} - YouTube",
                f"https://www.youtube.com/watch?v={vid}&t=10s&list=SECRETLIST", step=10)


def outcomes(cfg):
    conn = sqlite3.connect(cfg.db_path)
    return [r[0] for r in conn.execute("SELECT outcome FROM kg_lookups ORDER BY id")]


def test_lookup_order_exact_channel_semantic_llm(cfg):
    llm = FakeLLM({"6.006": ("education", 0.95), "speedrun": ("entertainment", 0.9)})
    c, kg = make_client(cfg, llm)

    # 1) two videos from one channel -> 2 LLM calls; channel becomes consistently "education"
    post(c, yt("09:00", "vid0000001", "6.006 Session 1 Sorting and Searching"))
    post(c, yt("09:10", "vid0000002", "6.006 Session 2 Hashing"))
    assert c.post("/kg/process").json()["llm"] == 2
    assert len(llm.calls) == 2

    # 2) exact: same video again -> served from the graph, no call
    post(c, yt("10:00", "vid0000001", "6.006 Session 1 Sorting and Searching"))
    # 3) parent: a NEW video of the same channel with an unrelated title -> inherits the channel
    post(c, yt("10:10", "vid0000003", "Office hours recording"))
    c.post("/kg/process")
    # 4) semantic: a different channel, but a near-identical title -> similar-title hit
    post(c, yt("10:20", "vid0000004", "6.006 Session 1 Sorting and Searching (reupload)"))
    c.post("/kg/process")
    assert len(llm.calls) == 2, "exact / channel / semantic hits must not call the LLM"
    # 5) genuinely new -> LLM
    post(c, yt("10:30", "vid0000005", "Any% speedrun world record"))
    c.post("/kg/process")
    assert len(llm.calls) == 3

    assert outcomes(cfg) == ["llm", "llm", "exact", "parent", "semantic", "llm"]
    stats = c.get("/kg/stats").json()
    assert stats["llm_calls_saved"] == 3 and stats["llm_answers"] == 3 and stats["cache_hit_rate"] == 0.5

    # the classification flows into the dashboard: 09:00 window becomes General Learning, not Movies
    ws = c.get("/windows", params={"start": DAY, "end": DAY}).json()["windows"]
    by_start = {w["window_start"]: w for w in ws}
    assert by_start[local_ts("09:00")]["category"] == "Learning › YT videos"     # a YouTube lesson
    assert by_start[local_ts("10:30")]["category"] == "Watching › Random YT videos"


def test_second_similar_title_makes_zero_llm_calls(cfg):
    llm = FakeLLM({"podcast": ("education", 0.9)})
    c, kg = make_client(cfg, llm)
    post(c, span("09:00", 5, "Google Chrome", "Deep Talks Podcast Episode 41 with Jane Doe",
                 "https://unknownsite.example/episodes/41", step=10))
    c.post("/kg/process")
    assert len(llm.calls) == 1
    post(c, span("09:30", 5, "Google Chrome", "Deep Talks Podcast Episode 42 with Jane Doe",
                 "https://othersite.example/ep/42", step=10))
    c.post("/kg/process")
    assert len(llm.calls) == 1
    assert outcomes(cfg)[-1] == "semantic"


def test_llm_payload_has_no_url_or_query(cfg):
    llm = FakeLLM({"x": ("education", 0.9)})
    c, kg = make_client(cfg, llm)
    post(c, yt("09:00", "vid0000001", "Secret talk"))
    post(c, span("09:10", 5, "Google Chrome", "Pricing page", "https://saas.example/pricing/team?ref=SECRET&utm=1#x",
                 step=10))
    post(c, span("09:20", 5, "SomeNewApp", "private-document-name.docx", step=10))
    c.post("/kg/process")
    assert len(llm.calls) == 3
    for payload in llm.calls:
        assert set(payload) <= SAFE_KEYS
        blob = json.dumps(payload)
        for bad in ("http", "?", "&", "#", "/watch", "vid0000001", "SECRET", "utm", "/pricing", "SECRETLIST",
                    "private-document-name"):
            assert bad not in blob, (bad, blob)
    assert llm.calls[0] == {"title": "Secret talk", "site": "youtube.com", "channel": "MIT OpenCourseWare"}
    assert llm.calls[2] == {"app": "SomeNewApp"}  # app window titles never leave the server


def test_real_http_request_body_is_url_free(cfg):
    """The actual bytes sent to the provider (Groq-compatible API) contain no URL/query."""
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = request.content.decode()
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
            {"signal": "education", "confidence": 0.9, "reason": "lecture"})}}]})

    prov = OpenAICompatibleProvider("https://api.groq.com/openai/v1", "m", "KEY", transport=httpx.MockTransport(handler))
    c, kg = make_client(cfg, prov)
    post(c, yt("09:00", "vid0000001", "Graph traversal walk-through"))
    assert c.post("/kg/process").json()["llm"] == 1
    body = seen["body"]
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert "vid0000001" not in body and "SECRETLIST" not in body and "watch?" not in body
    user_msg = json.loads(json.loads(body)["messages"][1]["content"])
    assert user_msg == {"title": "Graph traversal walk-through", "site": "youtube.com", "channel": "MIT OpenCourseWare"}


def test_build_payload_drops_urlish_values():
    p = build_payload("Read https://evil.example/a?b=c now", "evil.example/path?x", None, None)
    assert p == {"title": "Read now"}


@pytest.mark.parametrize("raw", [
    {"signal": "hacking", "confidence": 0.9}, {"signal": "education", "confidence": 3},
    {"signal": "education"}, ["education"], {"signal": None, "confidence": 0.5},
])
def test_invalid_llm_answers_rejected(raw):
    with pytest.raises(LLMError):
        validate_answer(raw, ["education", "entertainment", "other"])


def test_invalid_answer_keeps_item_unknown(cfg):
    llm = FakeLLM(raw={"signal": "not-a-signal", "confidence": 0.9, "reason": "x"})
    c, kg = make_client(cfg, llm)
    post(c, yt("09:00", "vid0000004", "Mystery video"))
    for _ in range(3):
        c.post("/kg/process")
    review = c.get("/kg/review").json()
    assert review[0]["signal"] is None and review[0]["queue_status"] == "failed"
    conn = sqlite3.connect(cfg.db_path)
    assert conn.execute("SELECT COUNT(*), SUM(ok) FROM llm_calls").fetchone() == (3, 0)


def test_no_key_dry_run_and_budget_queue_items(cfg):
    c, kg = make_client(cfg, None)  # no API key
    post(c, yt("09:00", "vid0000004", "Mystery video"))
    assert c.post("/kg/process").json()["queued"] == 1
    assert outcomes(cfg) == ["queued"]
    assert c.get("/kg/review").json()[0]["queue_status"] == "pending"

    llm = FakeLLM({"mystery": ("entertainment", 0.9)})
    kg.provider = llm
    kg.llm_cfg = dict(kg.llm_cfg, dry_run=True)
    c.post("/kg/process")
    assert llm.calls == []
    kg.llm_cfg = dict(kg.llm_cfg, dry_run=False, daily_budget=0)
    c.post("/kg/process")
    assert llm.calls == []
    kg.llm_cfg = dict(kg.llm_cfg, daily_budget=10)
    assert c.post("/kg/process").json()["llm"] == 1
    assert outcomes(cfg) == ["queued", "llm"]  # 'queued' logged once, not on every retry


def test_low_confidence_goes_to_review(cfg):
    llm = FakeLLM({"mystery": ("entertainment", 0.4)})
    c, kg = make_client(cfg, llm)
    post(c, yt("09:00", "vid0000004", "Mystery video"))
    c.post("/kg/process")
    r = c.get("/kg/review").json()
    assert len(r) == 1 and r[0]["needs_review"] and r[0]["signal"] == "entertainment"


def test_user_correction_wins_and_propagates_only_when_asked(cfg):
    llm = FakeLLM({"vlog": ("entertainment", 0.9)})
    c, kg = make_client(cfg, llm)
    post(c, yt("09:00", "vid0000001", "Campus vlog"))
    c.post("/kg/process")
    node = c.get("/kg/nodes", params={"type": "video"}).json()[0]
    assert node["signal"] == "entertainment"

    # correct the video only
    c.post("/kg/correct", json={"node_id": node["node_id"], "signal": "education"})
    assert c.get("/kg/nodes", params={"type": "video"}).json()[0]["source"] == "user"
    ws = c.get("/windows", params={"start": DAY, "end": DAY}).json()["windows"]
    assert ws[0]["category"] == "Learning › YT videos"
    # history kept: the LLM edge is deactivated, not deleted
    conn = sqlite3.connect(cfg.db_path)
    assert conn.execute("SELECT COUNT(*) FROM kg_edges WHERE rel='classified_as' AND src_id=? AND active=0",
                        (node["node_id"],)).fetchone()[0] == 1
    # a new video of the same channel: channel has only 1 LLM vote -> not inherited -> LLM asked
    post(c, yt("09:30", "vid0000002", "Another vlog"))
    c.post("/kg/process")
    assert len(llm.calls) == 2

    # now tick "apply to whole channel": the next new video inherits education without an LLM call
    c.post("/kg/correct", json={"node_id": node["node_id"], "signal": "education", "apply_to_parent": True})
    post(c, yt("10:00", "vid0000003", "Third vlog"))
    c.post("/kg/process")
    assert len(llm.calls) == 2
    v3 = [n for n in c.get("/kg/nodes", params={"type": "video"}).json() if n["key"] == "vid0000003"][0]
    assert v3["signal"] is None  # nothing stored on the video itself...
    ws = c.get("/windows", params={"start": DAY, "end": DAY}).json()["windows"]
    assert {w["window_start"]: w for w in ws}[local_ts("10:00")]["category"] == "Learning › YT videos"  # ...inherited


def test_user_kg_correction_beats_rules(cfg):
    c, kg = make_client(cfg, FakeLLM())
    # Netflix is an entertainment RULE (confidence 1.0). A KG user correction on that page still wins.
    post(c, span("09:00", 5, "Google Chrome", "Documentary", "https://www.netflix.com/watch/123", step=10))
    conn = sqlite3.connect(cfg.db_path)
    assert conn.execute("SELECT COUNT(*) FROM kg_nodes").fetchone()[0] == 0  # rule-known items skip the KG
    from server.app.kg.items import item_from_segment
    item = item_from_segment({"app": "Google Chrome", "url": "https://www.netflix.com/watch/123",
                              "domain": "netflix.com", "title": "Documentary"}, kg.browsers)
    from server.app.db import connect
    cc = connect(cfg.db_path)
    nid = kg.ensure_item(cc, item)
    kg.correct(cc, nid, "education")
    ws = c.get("/windows", params={"start": DAY, "end": DAY}).json()["windows"]
    assert ws[0]["category"] == "Learning › General learning"   # a Netflix documentary you marked as learning


def test_deactivate_keeps_row(cfg):
    llm = FakeLLM({"mystery": ("entertainment", 0.9)})
    c, kg = make_client(cfg, llm)
    post(c, yt("09:00", "vid0000004", "Mystery video"))
    c.post("/kg/process")
    node = c.get("/kg/nodes", params={"type": "video"}).json()[0]
    assert c.post(f"/kg/edges/{node['edge_id']}/deactivate").status_code == 200
    assert c.get("/kg/nodes", params={"type": "video"}).json()[0]["signal"] is None
    conn = sqlite3.connect(cfg.db_path)
    assert conn.execute("SELECT active FROM kg_edges WHERE id=?", (node["edge_id"],)).fetchone() == (0,)


def test_backfill_existing_heartbeats(cfg):
    # heartbeats stored before phase 2 never went through the KG; the one-time backfill picks them up
    from server.app.classify.pipeline import Classifier
    from server.app.db import connect, init_db
    from server.app.ingest import ingest
    init_db(cfg)
    conn = connect(cfg.db_path)
    ingest(conn, cfg, yt("09:00", "vid0000004", "Old mystery video")
           + span("09:10", 5, "Code", "main.py", step=10), device="mac-test")  # Code = known coding rule
    assert conn.execute("SELECT COUNT(*) FROM kg_nodes").fetchone()[0] == 0
    llm = FakeLLM({"mystery": ("entertainment", 0.9)})
    graph = KnowledgeGraph(cfg, provider=llm, embedder=HashEmbedder(), channel_fetcher=lambda v: None)
    assert graph.backfill(conn, Classifier(cfg).rule_signal) == 1  # only the rule-unclear video
    assert graph.process_queue(conn)["llm"] == 1
    assert llm.calls == [{"title": "Old mystery video", "site": "youtube.com"}]


def test_kg_endpoints_require_admin(app):
    with TestClient(app) as c:
        for path in ("/kg/stats", "/kg/review", "/kg/nodes", "/kg/lookups"):
            assert c.get(path).status_code == 401


def _search(start, query, engine="https://www.google.com/search?q=SECRETQUERY&hl=en"):
    return span(start, 2, "Google Chrome", f"{query} - Google Search", engine, step=10)


def test_search_pages_never_reach_the_llm_and_are_per_search(cfg):
    llm = FakeLLM({"python": ("coding", 0.9), "flu": ("admin", 0.9)})
    c, kg = make_client(cfg, llm)
    post(c, _search("09:00", "how to add python to screen recording permission"))
    post(c, _search("09:05", "flu symptoms and fever treatment"))
    post(c, _search("09:10", "best pizza near me", "https://duckduckgo.com/?q=pizza"))
    c.post("/kg/process")
    assert llm.calls == [], "search words must never be sent to the LLM"
    nodes = c.get("/kg/nodes", params={"type": "search"}).json()
    assert len(nodes) == 3 and all(n["signal"] is None for n in nodes)  # separate items, stay unknown
    assert outcomes(cfg).count("local-only") == 3
    assert c.get("/kg/review").json() == []  # not dumped into the review queue
    conn = sqlite3.connect(cfg.db_path)
    assert conn.execute("SELECT COUNT(*) FROM kg_nodes WHERE key LIKE '%SECRETQUERY%'").fetchone()[0] == 0


def test_search_classified_locally_after_you_label_a_similar_one(cfg):
    llm = FakeLLM()
    c, kg = make_client(cfg, llm)
    post(c, _search("09:00", "python screen recording permission macos"))
    c.post("/kg/process")
    node = c.get("/kg/nodes", params={"type": "search"}).json()[0]
    c.post("/kg/correct", json={"node_id": node["node_id"], "signal": "coding"})
    post(c, _search("09:30", "python screen recording permission macos sonoma"))  # near-identical search
    c.post("/kg/process")
    assert llm.calls == []
    assert outcomes(cfg)[-1] == "semantic"
    newest = [n for n in c.get("/kg/nodes", params={"type": "search"}).json() if "sonoma" in n["label"]][0]
    assert newest["signal"] == "coding" and newest["source"] == "semantic"


def test_old_shared_search_node_is_retired(cfg):
    from server.app.db import connect, init_db
    init_db(cfg)
    kg = KnowledgeGraph(cfg, provider=None, embedder=HashEmbedder(), channel_fetcher=None)
    conn = connect(cfg.db_path)
    nid = kg._upsert_node(conn, "page", "google.com/search", "old search", "google.com")
    kg._write_class(conn, nid, "education", 0.95, "llm", "x")
    assert kg.retire_collapsed_search_pages(conn) == 1
    assert kg.effective(conn, nid) is None
    assert conn.execute("SELECT COUNT(*) FROM kg_edges WHERE src_id=?", (nid,)).fetchone()[0] == 1  # kept
