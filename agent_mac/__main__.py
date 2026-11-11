"""python -m agent_mac <command>

  run       start collecting (what launchd runs)
  pause     stop collecting immediately (works offline)
  resume    resume collecting
  status    show pause state, buffered heartbeats, server reachability
  probe     print what the agent would record right now (permissions check)
"""
from __future__ import annotations

import logging
import sys
import time

from .agent import CONFIG_PATH, PAUSE_FLAG, SUPPORT_DIR, load_agent_config


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "help"
    if cmd == "run":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        from .agent import run
        run()
    elif cmd == "pause":
        SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
        PAUSE_FLAG.write_text(str(time.time()))
        print("tracking paused on this Mac (python -m agent_mac resume to continue)")
    elif cmd == "resume":
        PAUSE_FLAG.unlink(missing_ok=True)
        print("tracking resumed on this Mac")
    elif cmd == "status":
        from .buffer import Buffer
        cfg = load_agent_config()
        print(f"config:  {CONFIG_PATH} ({'found' if CONFIG_PATH.exists() else 'MISSING'})")
        print(f"paused locally: {PAUSE_FLAG.exists()}")
        print(f"buffered heartbeats: {Buffer(SUPPORT_DIR / 'buffer.db').count()}")
        try:
            import httpx
            r = httpx.get(cfg["server_url"].rstrip("/") + "/status", timeout=10,
                          headers={"Authorization": f"Bearer {cfg['token']}"})
            print(f"server: {r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"server: unreachable ({e})")
    elif cmd == "probe":
        from . import macos
        from .agent import Collector
        from .idle import parse_pmset_assertions
        cfg = load_agent_config()
        front = macos.frontmost()
        print(f"frontmost: {front}")
        print(f"window title visible: {bool(front and front.title)}  (False => grant Screen Recording)")
        print(f"input idle: {macos.input_idle_seconds():.1f}s  locked: {macos.screen_locked()}  "
              f"battery: {macos.on_battery()}")
        print(f"assertions: {parse_pmset_assertions(macos.pmset_assertions_text())}")
        print(f"sample: {Collector(cfg).sample()}")
    else:
        print(__doc__)
        return 0 if cmd == "help" else 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
