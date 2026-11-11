#!/usr/bin/env bash
# Deploy / update the server from this Mac:  ./deploy/deploy.sh
# Safe to re-run. Never touches ./data on the server and never deletes server files.
set -euo pipefail

HOST="${HT_HOST:-user@100.x.y.z}"
DIR="${HT_DIR:-habit-tracker}"
TS_NAME="${HT_TS_NAME:-your-server.your-tailnet.ts.net}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> 1/6 copying code to $HOST:~/$DIR"
ssh "$HOST" "mkdir -p ~/$DIR/data/backups"
rsync -az --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.pyc' \
  server deploy requirements-server.txt "$HOST:$DIR/"
# dashboard/ and web/ are pure code: mirror them exactly so renamed/removed files don't linger (never used for data)
rsync -az --delete --exclude '__pycache__' dashboard/ "$HOST:$DIR/dashboard/"
rsync -az --delete --exclude 'node_modules' --exclude 'dist' web/ "$HOST:$DIR/web/"

APK="android/app/build/outputs/apk/debug/app-debug.apk"
ssh "$HOST" "mkdir -p ~/$DIR/dist"
if [ -f "$APK" ]; then
  python3 - "$APK" > /tmp/ht-apk.json <<'PY'
import hashlib, json, pathlib, sys
apk = pathlib.Path(sys.argv[1])
meta = json.loads((apk.parent / "output-metadata.json").read_text())["elements"][0]
print(json.dumps({"version_code": meta["versionCode"], "version_name": meta["versionName"],
                  "sha256": hashlib.sha256(apk.read_bytes()).hexdigest()}))
PY
  rsync -az "$APK" "$HOST:$DIR/dist/habit-tracker.apk.new" && rsync -az /tmp/ht-apk.json "$HOST:$DIR/dist/habit-tracker.json.new"
  ssh "$HOST" "cd ~/$DIR/dist && mv habit-tracker.apk.new habit-tracker.apk && mv habit-tracker.json.new habit-tracker.json"
  echo "    android app $(python3 -c 'import json;print(json.load(open("/tmp/ht-apk.json"))["version_name"])') published"
fi

echo "==> 2/6 server .env (created once; existing file is kept)"
if ! ssh "$HOST" "test -f ~/$DIR/.env"; then
  # Read the Groq key from the local .env without printing it; generate an admin token; restrict
  # the dashboard to the tailnet owner's login.
  OWNER="$(ssh "$HOST" "tailscale status --json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["User"][str(d["Self"]["UserID"])]["LoginName"])')"
  python3 - "$OWNER" <<'PY' | ssh "$HOST" "umask 077; cat > ~/$DIR/.env"
import secrets, sys, pathlib
env = {}
p = pathlib.Path(".env")
if p.exists():
    for line in p.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
print(f"HT_ADMIN_TOKEN={secrets.token_urlsafe(32)}")
print(f"HT_OWNER_LOGIN={sys.argv[1]}")
print(f"GROQ_API_KEY={env.get('GROQ_API_KEY', '')}")
print("TZ=UTC")
PY
  echo "    created ~/$DIR/.env (mode 600)"
fi

# Separate, empty Docker client config: public images are pulled anonymously, so stale Docker Hub
# credentials in the default config can't break the build (and we never touch them).
DC="export DOCKER_CONFIG=\$HOME/$DIR/docker-config; mkdir -p \$DOCKER_CONFIG; [ -f \$DOCKER_CONFIG/config.json ] || echo '{}' > \$DOCKER_CONFIG/config.json; cd ~/$DIR"

echo "==> 3/6 building and starting containers"
ssh "$HOST" "$DC && docker compose -f deploy/docker-compose.yml up -d --build"

echo "==> 4/6 running the test suite inside the container"
ssh "$HOST" "$DC && docker compose -f deploy/docker-compose.yml run --rm -T -e HT_DISABLE_BACKGROUND=1 api python -m pytest server/tests -q -p no:cacheprovider -p no:warnings"

echo "==> 5/6 tailscale serve (tailnet only, no Funnel): api :8765, dashboard https :8501 + http :8051"
ssh "$HOST" "tailscale serve --bg --https=8765 http://127.0.0.1:18765 >/dev/null && tailscale serve --bg --https=8501 http://127.0.0.1:18501 >/dev/null && tailscale serve --bg --http=8051 http://127.0.0.1:18501 >/dev/null && tailscale serve status"

echo "==> 6/6 checking from this Mac over the tailnet"
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS "https://$TS_NAME:8765/health" >/dev/null 2>&1; then break; fi; sleep 3
done
curl -fsS "https://$TS_NAME:8765/health"; echo
echo "    dashboard: $(curl -s -o /dev/null -w '%{http_code}' "https://$TS_NAME:8501/")"
echo "    web app:   $(curl -s -o /dev/null -w '%{http_code}' "https://$TS_NAME:8765/ui/")"
echo "done. Web app: https://$TS_NAME:8765/ui/   Dashboard (Streamlit, until every page has moved): https://$TS_NAME:8501"
