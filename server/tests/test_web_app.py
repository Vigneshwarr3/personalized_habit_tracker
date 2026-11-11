"""The web app: owner login via the Tailscale stamp (with the cross-site guard) and serving web/dist at /ui/."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.main import create_app

OWNER = "owner@example.com"
SAME = {"Tailscale-User-Login": OWNER, "Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def web_dir(tmp_path, monkeypatch):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><div id=root></div>")
    (d / "assets" / "app-abc123.js").write_text("console.log(1)")
    (d / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("outside the build folder")
    monkeypatch.setenv("HT_WEB_DIR", str(d))
    return d


@pytest.fixture
def anon(cfg, kg, web_dir):
    """A client with no token: like the browser."""
    cfg.owner_login = OWNER
    with TestClient(create_app(cfg, start_background=False, kg=kg)) as c:
        yield c


def test_owner_stamp_from_own_page_is_admin(anon):
    r = anon.get("/status", headers=SAME)
    assert r.status_code == 200 and r.json()["you"]["role"] == "admin"
    assert anon.get("/categories", headers=SAME).status_code == 200


def test_owner_login_is_case_insensitive(anon):
    assert anon.get("/status", headers=SAME | {"Tailscale-User-Login": "Owner@Example.COM"}).status_code == 200


@pytest.mark.parametrize("headers", [
    {},                                                                    # no stamp at all
    {"Tailscale-User-Login": "someone-else@example.com", "Sec-Fetch-Site": "same-origin"},  # another tailnet user
    {"Tailscale-User-Login": OWNER},                                       # not from a browser page
    {"Tailscale-User-Login": OWNER, "Sec-Fetch-Site": "cross-site"},       # another website (CSRF)
    {"Tailscale-User-Login": OWNER, "Sec-Fetch-Site": "same-site"},        # another port/subdomain of the server
    {"Tailscale-User-Login": OWNER, "Sec-Fetch-Site": "none"},             # typed into the address bar
])
def test_refused_without_owner_stamp_from_own_page(anon, headers):
    assert anon.get("/status", headers=headers).status_code == 401
    assert anon.post("/labels", headers=headers,
                     json={"start_ts": 1, "end_ts": 2, "category": 1}).status_code == 401


def test_cross_site_write_is_refused(anon):
    """A page on another site trying to paint a label as you."""
    r = anon.post("/pause", json={"paused": True},
                  headers={"Tailscale-User-Login": OWNER, "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 401
    assert anon.get("/status", headers=SAME).json()["paused"] is False


def test_no_owner_configured_means_no_stamp_login(cfg, kg, web_dir):
    cfg.owner_login = None
    with TestClient(create_app(cfg, start_background=False, kg=kg)) as c:
        assert c.get("/status", headers=SAME).status_code == 401


def test_bad_token_does_not_fall_back_to_stamp(anon):
    assert anon.get("/status", headers=SAME | {"Authorization": "Bearer nope"}).status_code == 401


def test_device_tokens_still_work(anon):
    from .conftest import ADMIN
    tok = anon.post("/devices", json={"name": "mac-1", "source": "mac"},
                    headers={"Authorization": f"Bearer {ADMIN}"}).json()["token"]
    r = anon.get("/status", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["you"] == {"role": "device", "device": "mac-1"}
    # a device token still can't do admin things, even with the owner stamp alongside
    assert anon.get("/categories", headers=SAME | {"Authorization": f"Bearer {tok}"}).status_code == 403


# ---- serving the built app
def test_root_redirects_to_ui(anon):
    for path in ("/", "/ui"):
        r = anon.get(path, follow_redirects=False)
        assert r.status_code in (302, 307) and r.headers["location"] == "/ui/"


@pytest.mark.parametrize("path", ["/ui/", "/ui/corrections", "/ui/some/deep/page"])
def test_pages_get_index_html(anon, path):
    r = anon.get(path)
    assert r.status_code == 200 and "<div id=root>" in r.text
    assert r.headers["cache-control"] == "no-cache"


def test_hashed_assets_are_cached_forever(anon):
    r = anon.get("/ui/assets/app-abc123.js")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    assert anon.get("/ui/favicon.svg").headers["cache-control"] == "no-cache"


def test_missing_asset_is_404_not_index(anon):
    assert anon.get("/ui/assets/gone-123.js").status_code == 404


@pytest.mark.parametrize("path", ["/ui/../secret.txt", "/ui/%2e%2e/secret.txt", "/ui/..%2fsecret.txt"])
def test_no_files_outside_the_build(anon, path):
    r = anon.get(path)
    assert "outside the build folder" not in r.text


def test_unbuilt_app_explains(cfg, kg, tmp_path, monkeypatch):
    monkeypatch.setenv("HT_WEB_DIR", str(tmp_path / "nothing"))
    with TestClient(create_app(cfg, start_background=False, kg=kg)) as c:
        r = c.get("/ui/")
        assert r.status_code == 404 and "not built" in r.text


# ---- parallel requests (the web app asks for several things at once)
def test_connection_can_move_between_threads(cfg):
    """FastAPI may open a request's DB connection on one thread and use it on another."""
    import threading
    from server.app.db import connect
    conn = connect(cfg.db_path)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("v", conn.execute("SELECT 1").fetchone()[0]))
    t.start(); t.join()
    conn.close()
    assert out["v"] == 1


def test_parallel_requests(client):
    from concurrent.futures import ThreadPoolExecutor
    from .conftest import post, synthetic_day
    post(client, synthetic_day())
    paths = ["/dashboard?start=2026-11-01&end=2026-11-07", "/kpis?start=2026-11-01&end=2026-11-07", "/status",
             "/categories"] * 4
    with ThreadPoolExecutor(8) as ex:
        codes = list(ex.map(lambda p: client.get(p).status_code, paths))
    assert codes == [200] * len(paths)
