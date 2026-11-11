"""Admin CLI.  python -m server.app.cli <command>

  add-device <name> <source>   create a collector token (source: mac | android | ios)
  list-devices
  revoke-device <name>
  backup                       write a backup right now
  check-config                 validate rules.yaml / routines.yaml
"""
from __future__ import annotations

import sys
from datetime import datetime

from . import auth
from .backup import backup_now
from .classify.pipeline import Classifier
from .config import load_config
from .db import connect, init_db


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cfg = load_config()
    init_db(cfg)
    cmd, args = argv[0], argv[1:]
    conn = connect(cfg.db_path)
    try:
        if cmd == "add-device" and len(args) == 2:
            token = auth.create_device(conn, args[0], args[1])
            print(f"device {args[0]!r} created. Token (shown once, store it now):\n{token}")
        elif cmd == "list-devices":
            for r in conn.execute("SELECT name, source, created_at, revoked_at, last_seen FROM devices"):
                fmt = lambda t: datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M") if t else "-"
                print(f"{r['name']:20} {r['source']:8} created {fmt(r['created_at'])}  "
                      f"last seen {fmt(r['last_seen'])}  {'REVOKED' if r['revoked_at'] else 'active'}")
        elif cmd == "revoke-device" and len(args) == 1:
            print("revoked" if auth.revoke_device(conn, args[0]) else "no such active device")
        elif cmd == "backup":
            label = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            print(backup_now(cfg, label=label))
        elif cmd == "check-config":
            Classifier(cfg)
            print(f"OK: {len(cfg.rules)} rules, {len(cfg.routines)} routines, {len(cfg.signals)} signals")
        else:
            print(__doc__)
            return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
