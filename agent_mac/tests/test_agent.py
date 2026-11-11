"""Agent logic that doesn't need macOS: idle decision, pmset parsing, span merging, offline buffer, tab parsing."""
from __future__ import annotations

from agent_mac.agent import Sample, Spanner
from agent_mac.browsers import SEP, parse_result
from agent_mac.buffer import Buffer
from agent_mac.idle import decide, parse_pmset_assertions

PMSET_IDLE = """Assertion status system-wide:
   PreventUserIdleDisplaySleep    0
   PreventUserIdleSystemSleep     1
Listed by owning process:
   pid 359(powerd): [0x0006c6fa00019d5c] 00:48:56 PreventUserIdleSystemSleep named: "Powerd - Prevent sleep while display is on"
   pid 416(WindowServer): [0x0000004c0009807a] 00:00:02 UserIsActive named: "com.apple.iohideventsystem.queue.tickle"
"""
PMSET_VIDEO = PMSET_IDLE + (
    '   pid 901(Google Chrome Helper): [0x00000a0d00098623] 01:12:00 PreventUserIdleDisplaySleep named: "Video Wake Lock"\n')
PMSET_AUDIO = PMSET_IDLE + (
    '   pid 300(coreaudiod): [0x00000a0d00098624] 00:05:00 PreventUserIdleSystemSleep named: '
    '"com.apple.audio.BuiltInSpeakerDevice.context.preventuseridlesleep"\n')
PMSET_CAFFEINATE = PMSET_IDLE + (
    '   pid 777(caffeinate): [0x00000a0d00098625] 05:00:00 PreventUserIdleDisplaySleep named: "caffeinate command-line tool"\n')


def test_pmset_parsing():
    assert parse_pmset_assertions(PMSET_IDLE).display_awake is False
    assert parse_pmset_assertions(PMSET_IDLE).audio_playing is False  # powerd's assertion is ignored
    assert parse_pmset_assertions(PMSET_VIDEO).display_awake is True
    assert parse_pmset_assertions(PMSET_AUDIO).audio_playing is True
    assert parse_pmset_assertions(PMSET_CAFFEINATE).display_awake is False  # keep-awake tools don't count


def test_idle_decision():
    s = decide(10, 120, False, None)
    assert (s.send, s.idle, s.media) == (True, False, False)
    s = decide(600, 120, False, parse_pmset_assertions(PMSET_IDLE))
    assert (s.send, s.idle) == (True, True)
    # watching a movie: no input for 10 min but video keeps the display awake -> NOT idle
    s = decide(600, 120, False, parse_pmset_assertions(PMSET_VIDEO))
    assert (s.send, s.idle, s.media) == (True, False, True)
    s = decide(600, 120, False, parse_pmset_assertions(PMSET_AUDIO))
    assert (s.idle, s.media) == (False, True)
    # locked -> send nothing
    assert decide(5, 120, True, None).send is False


def test_spanner_merges_and_splits():
    sp = Spanner("mac", max_span=60)
    a = Sample("Code", "x.py", None, False, False)
    b = Sample("Notes", "n", None, False, False)
    for i in range(6):            # 30 s of Code
        sp.add(a, 1000 + i * 5, 5)
    sp.add(b, 1030, 5)            # switch -> emits Code span
    sp.add(None, 1035, 5)         # locked -> emits Notes span
    sp.add(a, 2000, 5)            # after a gap -> new span
    sp.flush()
    out = sp.drain()
    assert [(h["app"], h["ts"], h["interval_s"]) for h in out] == [
        ("Code", 1000, 30), ("Notes", 1030, 5), ("Code", 2000, 5)]


def test_spanner_caps_span_length():
    sp = Spanner("mac", max_span=60)
    a = Sample("Code", "x.py", None, False, False)
    for i in range(30):           # 150 s
        sp.add(a, i * 5, 5)
    sp.flush()
    assert [h["interval_s"] for h in sp.drain()] == [60, 60, 30]


def test_buffer_keeps_batch_id_until_ack(tmp_path):
    buf = Buffer(tmp_path / "b.db")
    for i in range(3):
        buf.add({"i": i})
    bid, items = buf.next_batch(limit=2)
    buf.add({"i": 3})
    bid2, items2 = buf.next_batch(limit=2)   # retry after a failed upload -> same batch
    assert bid2 == bid and items2 == items == [{"i": 0}, {"i": 1}]
    buf.ack(bid)
    bid3, items3 = buf.next_batch(limit=10)
    assert bid3 != bid and items3 == [{"i": 2}, {"i": 3}]
    assert buf.count() == 2


def test_parse_tab_and_incognito():
    t = parse_result(f"normal{SEP}https://x.com/a?b=1{SEP}Title")
    assert (t.url, t.title, t.private) == ("https://x.com/a?b=1", "Title", False)
    assert parse_result(f"incognito{SEP}https://x.com{SEP}Secret").private is True
    assert parse_result("") is None and parse_result("garbage") is None


def test_privacy_filter_on_the_mac():
    from agent_mac.privacy import PrivacyFilter
    f = PrivacyFilter()
    # bank: bare site only, no title
    assert f.redact("Google Chrome", "https://secure.chase.com/accounts/4821?x=1", "Chase - Account ending 4821") == \
        ("https://secure.chase.com/", None)
    # password manager app: no title
    assert f.redact("1Password 8", None, "Personal vault - Bank login") == (None, None)
    # sign-in title on an ordinary site
    assert f.redact("Google Chrome", "https://github.com/login", "Sign in to GitHub") == ("https://github.com/", None)
    # ordinary page untouched; YouTube video titles are not sensitive
    assert f.redact("Code", None, "main.py — project") == (None, "main.py — project")
    yt = "https://www.youtube.com/watch?v=abc"
    assert f.redact("Google Chrome", yt, "Build a login page") == (yt, "Build a login page")
    # rules from the server replace the defaults
    f.set_rules({"private_apps": ["Notes"], "private_domains": [], "private_title_regex": None})
    assert f.redact("Notes", None, "Diary") == (None, None)
    assert f.redact("Google Chrome", "https://secure.chase.com/a", "x") == ("https://secure.chase.com/a", "x")
