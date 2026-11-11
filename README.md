# Habit Tracker

A personal activity tracker. It records what you do on your Mac (and later your Android phone) and turns it into **routines/categories**: an hour-by-day heatmap plus a pie chart of hours per category, like a hand-colored spreadsheet but filled in automatically.

```
Mac agent (launchd) ──┐  HTTPS over Tailscale + per-device token
Android (phase 3) ────┼─▶ https://your-server.your-tailnet.ts.net:8765   API (FastAPI + SQLite/WAL)
Phone / Mac browser ──┴─▶ https://your-server.your-tailnet.ts.net:8765/ui/  web app (React, served by the API)
                          https://your-server.your-tailnet.ts.net:8501       old dashboard (Streamlit, until every page has moved)

server (Docker Compose, ~/habit-tracker):  api (+ web app) · dashboard · data/tracker.db · data/backups/
```

Everything runs on your own machines and nothing goes to a cloud database. The ports are only reachable from your tailnet: they're published with `tailscale serve`, never with Funnel. The dashboard only answers your own Tailscale login.

---

## Compatibility

| Part | Works with | Notes |
|---|---|---|
| **Collector (Mac)** | macOS on Apple Silicon or Intel, Python 3.11+ | Needs the **Screen Recording** and **Automation** permissions (see [Permissions](#permissions-macos-asks-the-first-time-you-can-change-them-in-system-settings--privacy--security)). |
| **Collector (phone)** | Android 8+ (built and tested on a Pixel) | Needs **Usage access**, plus optional **Location**, **Physical activity** and **Notifications** permissions (see [Android app](#3b-android-app-phase-3-collector--dashboard)). iOS isn't supported yet. |
| **Server + dashboard** | Any always-on Linux machine with Docker Compose | Reached over **Tailscale** (tailnet-only, never public). |

> **Not supported:** Windows and Linux desktop collectors. The Mac agent relies on macOS APIs (AppleScript, `pmset`, CoreGraphics).

## Getting started from a fresh clone

This repo contains no personal data, keys or server addresses. Every address in the code and docs is a placeholder (`your-server.your-tailnet.ts.net`, `100.x.y.z`, `user@`). Replace them with your own.

**You need:**
- a [Tailscale](https://tailscale.com) account with the server, your Mac and your phone on the same tailnet (HTTPS certificates enabled in the Tailscale admin console)
- an always-on Linux server with Docker + Docker Compose and SSH access from your Mac
- Python 3.11 on the Mac
- optional: a [Groq](https://console.groq.com) API key for the LLM fallback (the tracker works without it)
- optional, only to build the Android app: JDK 17 and the Android SDK

**Steps:**
1. Clone the repo:
   ```bash
   git clone https://github.com/Vigneshwarr3/personalized_habit_tracker.git habit-tracker
   cd habit-tracker
   ```
2. Create your local secrets file. It is gitignored, so it never gets committed:
   ```bash
   cp .env.example .env     # then put your GROQ_API_KEY in it (or leave it empty)
   ```
3. Point the project at *your* server. Either export these variables each time, or edit the defaults:

   | Setting | Variable | Default to edit |
   |---|---|---|
   | SSH target of the server | `HT_HOST` | `deploy/deploy.sh` |
   | Tailscale name of the server | `HT_TS_NAME` | `deploy/deploy.sh` |
   | Server API URL for the Mac agent | `HT_SERVER_URL` | `agent_mac/install.sh`, `agent_mac/agent.py` |
   | Server backup location to pull from | `HT_BACKUP_SRC` | `agent_mac/install.sh` |
   | Android default URLs (they can also be changed in the app's Settings) | none | `Prefs.kt`, `SettingsActivity.kt` in `android/app/src/main/java/com/habittracker/collector/` |

4. Edit `server/config/settings.yaml` (your `timezone`) and the privacy lists. `rules.yaml` and `routines.yaml` are starting points from the original author; change the domains (for example the university sites) to yours.
5. Follow sections 1 to 3b below: deploy the server, install the Mac agent, then set up the phone.

**Never commit** `.env`, `data/` (your database, backups and models), `android/local.properties`, or any device token. They're all in `.gitignore`. Device tokens are created in the dashboard and only live in each device's own config.

---

## 1. Server setup (one time)

**a. Let your user run Docker and change Tailscale's serve settings** (both need your sudo password):

```bash
ssh -t user@100.x.y.z 'sudo usermod -aG docker user && sudo tailscale set --operator=user'
```

**b. Deploy** from this Mac. Re-run the same command to update:

```bash
./deploy/deploy.sh
```

The script:
1. Copies the code to `~/habit-tracker`.
2. Creates `~/habit-tracker/.env` once (mode 600) with a random `HT_ADMIN_TOKEN`, your Tailscale login, and `GROQ_API_KEY` copied from the local `.env`.
3. Builds and starts both containers.
4. Runs the test suite inside the container.
5. Sets up `tailscale serve` on :8765 and :8501.
6. Checks `/health` from this Mac over the tailnet.

It never touches `data/` and never deletes files on the server.

**c. Create a token for this Mac:**

```bash
ssh user@100.x.y.z 'cd habit-tracker && docker compose -f deploy/docker-compose.yml exec api python -m server.app.cli add-device macbook-air mac'
```

You can also create tokens in the dashboard under **Privacy & devices → Create token**.

## 2. Mac agent

```bash
HT_DEVICE_TOKEN=ht_xxx ./agent_mac/install.sh
```

The installer:
- copies the agent to `~/Library/Application Support/HabitTracker`
- creates its Python environment
- writes `config.yaml` (mode 600)
- installs two launchd jobs that start at login:
  - **com.habittracker.agent**: the collector
  - **com.habittracker.backup-pull**: copies the server's nightly backups to `~/HabitTracker-Backups` every 6 h, never deleting anything

### Permissions (macOS asks the first time; you can change them in System Settings → Privacy & Security)

| Permission | Why | If denied |
|---|---|---|
| **Screen Recording** (for "Python") | Reading window *titles* via `CGWindowListCopyWindowInfo`. The agent never captures the screen. | Only app names are recorded, so classification is coarser. |
| **Automation → Google Chrome / Safari / Brave / Edge / Arc** | Reading the active tab's URL and title via AppleScript. | Falls back to the window title and retries every 10 min. |

Check what the agent sees right now:

```bash
cd ~/Library/Application\ Support/HabitTracker/app && ../venv/bin/python -m agent_mac probe
```

Other commands: `python -m agent_mac pause | resume | status`. Logs are in `~/Library/Logs/HabitTracker/agent.log`.

### Battery

| What | How it stays light |
|---|---|
| Sampling | Every 5 s on AC power, every 15 s on battery. Nothing runs while locked, asleep or paused. |
| Browser tab | Read in-process with `NSAppleScript` (no new `osascript` process each time), and only when a browser is in front and its title changed or 30 s have passed. |
| `pmset -g assertions` | Only run after 120 s without keyboard/mouse input, cached for 30 s. |
| Uploads | Identical samples are merged into spans on the Mac and sent in one batch a minute. |
| Process priority | launchd `ProcessType=Background`, `LowPriorityIO`, `Nice 10`. |
| Heavy work | All classification and storage happens on the server. |

### Idle rule

No keyboard/mouse input for more than 120 s counts as **idle**, unless one of these is true:
- an app holds `PreventUserIdleDisplaySleep` (a video playing in a browser or player)
- `coreaudiod` is playing audio

In those cases the time counts as **active** and is flagged `media` (watching a movie doesn't involve typing). A locked screen, `loginwindow`, or sleep records nothing. Assertions from `powerd`, `caffeinate`, Amphetamine and similar tools are ignored. Add your own in `ignore_assertion_processes`.

## 3. Phone dashboard

1. Install **Tailscale** on Android and log in with the same account.
2. Open `https://your-server.your-tailnet.ts.net:8501` and choose **Add to Home screen**.

Dashboard addresses (all tailnet-only, all served by `tailscale serve`, all with the owner check):

| URL | Notes |
|---|---|
| `https://your-server.your-tailnet.ts.net:8501` | Recommended (HTTPS, best for "Add to Home screen") |
| `http://server:8051` | Short version. Plain HTTP, but Tailscale already encrypts the traffic. |
| ~~`100.x.y.z:…`~~ | Doesn't work. The certificate is for the name, and `tailscale serve` routes by hostname. Exposing the container on the IP directly would skip the owner check. |

The layout adapts to the phone: the heatmap comes first and the pie chart sits underneath.

## 3b. Android app (phase 3): collector + dashboard

The app records **which app is in front and for how long**, as package names only: no titles, notifications, content or URLs. It uses Android's own usage log (`UsageStatsManager`).

**Battery**: no service runs all the time and there's no permanent notification. Every 15 minutes, WorkManager wakes a job (Android groups it with other background work). The job reads the new events, turns them into sessions, stores them in an offline queue, and uploads them.

**What counts**: screen off, lock or shutdown ends a session; the app that comes to the front starts a new one. Sessions longer than 50 minutes are sent in pieces and joined again on the server. While tracking is paused from the dashboard, the phone records nothing.

**Install (one time):**
1. Install **Tailscale** from the Play Store and log in with the same account.
2. On the phone, open `https://your-server.your-tailnet.ts.net:8501` → **Privacy & devices**:
   - Tap **Download the Android app**, and allow your browser to install unknown apps.
   - Create a token: name it `pixel-7a`, type `android`, and **copy** it.
3. Open **Habit Tracker** → Settings:
   1. **Grant usage access** and turn on Habit Tracker.
   2. Paste the token, set the device name to `pixel-7a`, then tap **Save**.
   - **Is the switch greyed out ("Restricted setting")?** Android blocks it for apps installed from a browser. Tap **Open app info** in the app's settings → ⋮ (top right) → **Allow restricted settings** → confirm with your PIN or fingerprint. Then go back and turn on usage access.
4. The app's home screen *is* the dashboard. Use the menu → **Settings & sync** for status: last sync, waiting uploads, and the last problem.

**Updating the app**:
1. Bump `versionCode` in `android/app/build.gradle.kts`, build, and run `./deploy/deploy.sh`.
2. On the phone, open Settings & sync → **Check for update**.
3. The app downloads the APK from your server, checks its SHA-256, and Android asks you to confirm. The first time, also allow "Install unknown apps" for Habit Tracker.

Your settings and token are kept across updates, as long as the APK is built on this Mac (same signing key, `~/.android/debug.keystore`).

To rebuild the app (the SDK was installed with Homebrew):
`cd android && JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home ./gradlew testDebugUnitTest assembleDebug`.
`./deploy/deploy.sh` publishes the new APK on the dashboard.

**Location (app 1.4+)**: one low-power fix every ~15 min, plus one when Android notices you've stopped after a ride.
- The fix is rounded to about 100 m **on the phone** before it's stored or sent. Precision is set in Settings → Location.
- Android's activity detection (vehicle / bike / walking / still) tells moving apart from staying.
- In the app's Settings:
  - **Allow location**, then choose *Allow all the time*.
  - **Allow physical activity**.
  - **Save current location as Home** and **…as Work** (150 m radius by default).
- How location affects categories:
  - It only fills blocks that are **empty or Unclassified**. Your labels and device activity win, so studying at the office stays General Learning.
  - At Work → **Office/Work**. Moving near home → **Commute**. More than 50 km from home → **Travel/Visiting places**.
  - A place you've labeled the same way twice → that category (can be switched off).
  - At Home → shown in the Location column only.
- Corrections has a **Location** column and lets you name places. The Overview has a **Commute time** card, which counts all commuting, even blocks where you were also on your phone.
- Coordinates are never sent to the LLM and never deleted.

**Limits**:
- The phone can't see *what* you watch inside YouTube, or which site is open in Chrome. Those are package-level only (`com.google.android.youtube` goes to the knowledge graph).
- The token is stored in the app's private storage, not in an encrypted keystore.
- The APK is debug-signed, which is fine for sideloading on your own phone.

## 4. Dashboard pages

**Activities › Sub-activities**: everything is filed under 11 activities:
- Watching, Learning, Project, Everyday activities, Travel, Sleep, Exercise, Unproductive, Work, Career, Friends/Family.
- Each activity has a color, and its sub-activities get shades of it. Change any color on **Categories**; sub-activities you haven't recolored follow their activity.
- The heatmap is colored by activity. The pie shows activities; tap a slice for its sub-activities.
- Categories from before activities stay as they were until you use **Categories → Move old categories**. Nothing is deleted, and moves can be undone.
- **Sessions** (Settings): watching for 2 h or more in one go counts as Unproductive › Binge watching. Social media or short videos for 30 min or more in one go count as Doom scrolling. The whole session counts, and labels always win.
- **Places** (Corrections → Places this day): name a place and choose what time there counts as. Places with the same name share the tag. **Open in OpenStreetMap** shows the spot. **Suggest a name** asks OpenStreetMap, sending only that place's rounded coordinates, and only when you press it.

**Look**: the Inter font, a graphite accent, and light/dark following your device, all set in `dashboard/.streamlit/config.toml`. In browsers the page links sit on the right of the header. In the Android app they're under ☰, together with **App settings & sync**.

- **Home**: hour × day heatmap (rows are hours, columns are dates, each cell is the category that took most of that hour; hover for the 15-min breakdown), plus a pie chart with "N Hours of Category, X%" labels.
- **Filling in time** (three ways, all saved as labels that override tracking):
  - **Paint** on the Overview heatmap: drag a box over hours (Mac) or tap an hour (phone), pick a category, and press **Paint**.
  - The Corrections **Day table** lists all 96 blocks of the day, including empty ones. Tick several rows and **Apply** one category.
  - Corrections **Quick fill**: *Fill all empty blocks* of the day, *Set 00:00–08:00*, or any custom range.
- **Settings**: show or hide each Overview card (Top 3, change vs previous period, group totals, time to cut, day coverage, phone vs laptop, longest focus, streak). Also: edit the category groups, choose which group is "time to cut", set the streak rule (default Learning ≥ 2 h/day), and choose which groups or categories count as focus. These are saved on the server, so they're the same on every device.
- **Corrections**: every 15-min window of a day with its category, the routine that chose it, and its top apps/titles. Change the category in the table and press **Save**, or label any time range by hand, including hours with no data. Corrections always win and are kept as training data. **Undo** deactivates a label; it isn't deleted.
- **Categories**: add, rename, recolor, archive, merge, unmerge. A rename keeps the old name as an alias, so YAML that uses it keeps working. A merge is a pointer that's resolved when data is read, so history is never rewritten.
- **Privacy & devices**: pause tracking, hide or un-hide a date range, create or revoke device tokens, storage stats, back up now.

## 4b. Web app (replacing the Streamlit dashboard)

The dashboard is moving from Streamlit to a regular web app in `web/` (React + TypeScript, built with Vite, styled with Tailwind + shadcn/ui, charts with Plotly). The same code can later be packaged as a desktop app (Tauri: Windows, macOS) and a mobile app (Capacitor: Android, iOS, iPadOS). The API serves it at `https://your-server.your-tailnet.ts.net:8765/ui/`. All seven pages have moved (Overview, Corrections, Categories, Knowledge graph, Model, Privacy & devices, Settings); the Streamlit dashboard still runs at :8501 until the Android app switches over. Saved changes show a short confirmation; anything that can't be undone (revoking a device) asks for a second click.

- **Opens instantly with your last data.** Each page first shows the data you saw last time (saved on the device, even after the app was closed), refreshes in the background, then swaps in the new data. A spinner next to the title means it's refreshing. Saved data older than 7 days is ignored.
- **Offline.** If the server can't be reached, the page keeps showing the saved data with an *"Offline, showing data from 10:42"* banner and a **Try again** button. The app's own files are kept on the device too, so it opens without a connection.
- **Error page.** Only when there's nothing saved to show: a plain message (can't reach the server / private / server problem / page problem), **Try again**, **Go to Overview**, and **Show details** for the technical error. Unknown addresses get a "Page not found" page.
- **Sign-in.** No token in the browser. Behind `tailscale serve`, Tailscale stamps each request with your login; the API accepts the stamp only if it's the owner's (`HT_OWNER_LOGIN` in the server's `.env`) and only for requests made by the app's own page (`Sec-Fetch-Site: same-origin`), so other websites you visit can't use it. Device tokens work as before.

**Develop** (Node 22):
```bash
cd web && npm install
HT_DEV_LOGIN=you@example.com npm run dev   # proxies to a local API on 127.0.0.1:18765 (owner login set to the same value)
HT_DEV_API=https://your-server.your-tailnet.ts.net:8765 npm run dev   # or: against the real server over Tailscale
npm test && npm run typecheck && npm run build
```
`./deploy/deploy.sh` copies `web/` to the server; the Docker image builds it.

## 5. Editing the classifier (no code changes)

On the server, edit `~/habit-tracker/server/config/*.yaml`, then run `docker compose -f deploy/docker-compose.yml restart api`. A bad YAML file makes startup fail with a clear message. To validate without restarting:
`docker compose -f deploy/docker-compose.yml exec api python -m server.app.cli check-config`.

- `settings.yaml`: timezone, window size, thresholds, the **signal list** (add new signals here), and which apps count as browsers.
- `rules.yaml`: app / domain / url_regex / title_regex mapped to a **signal**. First match wins. YouTube is split into education vs entertainment by title, and unclear videos get confidence 0.5 so the knowledge graph can take a second look in phase 2.
- `routines.yaml`: a window's signal mix and features mapped to a **category name**, for example `largest_context_share >= 0.7` and `signals.entertainment >= 0.6` means Movies. The available features are documented at the top of the file.
- **Negative Habits / Negative habits 2 have no rules or routines.** They're left for you to define.

Pipeline per 15-min window: **labels → rules → (phase 2: knowledge graph + LLM) → routines → (phase 4: learned model)**. Idle time is dropped, and windows with under 60 s of activity are blank.

## 6. Knowledge graph + LLM (phase 2)

Items the rules don't know, or know only with low confidence (for example YouTube videos whose title has no learning keywords), are classified into **signals** by a local knowledge graph. Groq is the fallback when the graph has no answer. The graph outputs signals, not categories, so renaming or merging categories never invalidates it.

**Lookup order per item**, logged in `kg_lookups`:

1. **Exact**: the same video or page was classified before.
2. **Channel / site**: the YouTube channel (looked up via `youtube.com/oembed`, which only receives the video id), then `site/first-path-segment`, then the domain. A parent is only used once it has at least 2 classified items and at least 80% of them agree. Platform domains (youtube.com, medium.com, reddit.com, ...) are never used as a whole; only their channels or sub-paths are.
3. **Similar title**: all-MiniLM-L6-v2 embeddings computed locally on the server, with cosine ≥ 0.80 (`semantic_threshold`).
4. **LLM**: Groq, via an OpenAI-compatible API. The model is set in `settings.yaml`; the default is `openai/gpt-oss-20b`, which your key supports.

**What the LLM receives**: `{"title", "site", "channel"}`, or `{"app"}` for desktop apps. It never sees URLs, paths, video ids, query strings, app window titles or any other activity, and tests check this against the actual HTTP request body. The answer must be valid JSON with a signal from your list, or it's rejected (an item is retried up to 3 times, then shown as failed in the review queue).

**Search pages are never sent to the LLM** (`local_only_pages`: Google, Bing, DuckDuckGo, Brave, YouTube search, ...), because their titles are your search words. Each search is its own item and is classified only locally, from similar searches you labeled. Otherwise it stays unknown, which costs little because searches are short.

**Limits**: `daily_budget` (200 calls a day), `max_per_minute`, and `dry_run`. With no API key, while offline, or once the budget is used up, items wait in the queue as "unknown" and are processed later.

**Every answer is stored** in the graph (node plus `classified_as` edge, and evidence for the channel or site), so the next similar item is answered locally. Answers with confidence below 0.6 go to the **review queue**.

**Your corrections always win**: they're stored with source=user and confidence 1.0, they override rules too, and they only apply to the whole channel or site if you tick that box. Removing a classification deactivates it; the history is kept.

The dashboard's **Knowledge graph** page shows:
- the share of lookups answered without the LLM, LLM calls saved vs made, and today's budget use
- a chart of how lookups were answered
- the review queue, browse & edit, and recent lookups

## 6b. Learned model (phase 4)

A LightGBM classifier trained on **your labeled 15-minute blocks**. Its inputs are the signal mix, the number of contexts, the share of the biggest context, switching, media, how long the current context has run, phone share, time of day and weekday.

- **Off until 300 labeled blocks** (`model.min_labeled_windows`). The fastest way to get there: open Corrections, fix the wrong rows, then press **Confirm this day**, which saves every remaining classified block of that day as correct.
- **Evaluation**: time-based cross-validation. It trains on earlier days and is tested on later days, never on the future. It's compared with the routines on the same held-out blocks, with precision, recall and F1 per category.
- **Serving**: the model is used **only for categories where its F1 beats the routines** (by 0.02, with at least 8 test blocks). It only replaces a routine answer that is Unclassified or one of those categories, and only with probability ≥ 0.5. Routines stay the fallback, and your labels always win.
- **Retrains nightly** at 04:00 on the server if your labels changed, or on demand with **Train now** on the **Model** page. That page also shows progress and the per-category comparison.

## 7. Privacy

**What each device can see at all:**
- **Mac:** the front app's name, the window title, and the browser's URL and tab title.
  - It never sees the screen contents (titles are read without capturing anything), keystrokes, passwords or form fields.
  - Incognito/private windows are skipped entirely.
- **Android:** the package name of the app in front, and for how long. No titles, notifications, content or URLs.

**Sensitive things**: `settings.yaml → privacy` lists:
- password managers and bank/wallet apps
- bank, payment, sign-in, health and tax sites
- a pattern for sensitive titles ("password", "sign in", "verification code", "account ending", ...)

For anything that matches:
- **The Mac** drops the title and cuts the URL to the bare site (`https://secure.chase.com/`) *before* the data is buffered or uploaded. The agent downloads the lists from the server every 30 min, and has built-in defaults when offline.
- **The server** applies the same filter again to everything it receives.
- **These items never enter the knowledge graph and never go to the LLM.** Their time still counts; for example, bank sites still count toward Bank/University/Hospital/Others.

Add your own bank, insurer or apps to the lists and run `deploy.sh`. The Mac picks the change up within 30 minutes.

- **Data is never deleted.** Heartbeats, labels, categories and hidden ranges are append-only, and "undo"/"un-hide" only set flags. A test fails if `DELETE FROM` / `DROP TABLE` appears anywhere in the server code.
- **Hide range**: removes a period from the dashboard and totals. The data stays and you can un-hide it.
- **Pause**: from the dashboard (collectors check it on every upload, about once a minute) or `python -m agent_mac pause` on the Mac (instant, works offline).
- **URL sanitization (on the server, for every heartbeat)**:
  - only http/https URLs are kept
  - `user:pass@`, query strings, fragments and `;jsessionid` are removed
  - the only query parameter ever kept is `?v=` on `youtube.com/watch`
  - `file://`, `chrome://` and `about:` URLs are dropped entirely
- **Incognito/private windows**:
  - Chrome, Brave, Edge and Arc report their window mode, so private windows are skipped completely.
  - Safari has no scripting API for private windows. Its private windows are recorded like normal ones, so pause tracking if that matters.
- **Tokens**: every client needs one, including localhost, because behind `tailscale serve` all traffic arrives from 127.0.0.1. Device tokens can only upload and read the pause flag, and the server decides the device name from the token. Tokens are stored hashed.
- **Backups**: a gzipped SQLite snapshot every night at 03:00 in `data/backups/`, never deleted, and copied to this Mac.

## 8. Tests

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements-server.txt -r requirements-agent.txt
.venv/bin/python -m pytest server/tests agent_mac/tests -q
cd web && npm test     # web app: dates in the tracker's timezone (incl. DST), ranges, heatmap painting, pie grouping, the Corrections day table
```

The tests use synthetic full-day heartbeat streams to cover:
- learning vs job applications vs movie windows
- YouTube split by title
- idle with media playing (counted as active) vs truly idle (dropped)
- merging device streams
- URL sanitization, and that no query string ever reaches the database
- category rename/merge/cycle guard/unmerge
- labels overriding routines, plus undo
- hidden ranges
- auth scopes and token revocation
- web app sign-in: owner stamp accepted only from the app's own page (other sites, other logins, no stamp and bad tokens are refused); serving `/ui/` (no files outside the build); parallel requests
- pause
- batch de-duplication
- backups never overwritten
- the no-delete guard
- agent span merging, the offline buffer, pmset parsing, incognito parsing
- **knowledge graph**:
  - lookup order exact → channel → similar title → LLM with a mocked LLM, including that a second similar title makes zero LLM calls
  - the LLM payload and the raw HTTP body contain no URL or query string
  - invalid LLM answers are rejected
  - no-key, dry-run and budget limits queue items instead of calling the LLM
  - low-confidence answers go to the review queue
  - corrections win and only apply to the whole channel/site when ticked
  - removing a classification keeps its history
  - the one-time backfill of earlier data

## Assumptions

- **Heatmap cell = dominant category of the hour.** Each hour is four 15-min windows, and the cell takes the category with the most windows (ties go to more active time). "Unclassified" is its own light-purple color, and blank means no data.
- **Pie hours = classified 15-min windows × 0.25 h**, including your manual labels. Unclassified time is listed under the chart, not in it.
- **Hours with no activity stay blank** (no automatic Sleep). Label them on the Corrections page if you want.
- **A window counts** if it has at least 60 s of non-idle activity. Overlapping time from several devices is merged, and context shares are by time.
- **Heartbeat storage**: back-to-back identical heartbeats from one device are stored as one span with a sample count. This keeps all the information and saves about 95% of the rows.
- **Timestamps** are stored in UTC and shown in `America/Indiana/Indianapolis` (`settings.yaml → timezone`).
- **Display-awake or audio-playing assertions** count as "media playing", so music while you're away from the keyboard counts as active time. Add players to `ignore_assertion_processes` if you'd rather not count that.
- **Mac offline buffer** is capped at 500 MB (months of data). If it fills up, collection stops; existing data is never dropped.
- **Server ports**: containers bind to `127.0.0.1:18765` and `127.0.0.1:18501`, and `tailscale serve` publishes them on the tailnet as :8765 and :8501. On this Mac, `127.0.0.1:8765` is already used by another project, which doesn't matter because the agent talks to the server.
- **Rule seeds** (job boards, learning sites, apps) are a starting point and can be edited in YAML. Zoom/Teams map to the `meeting` signal, which no routine uses yet because a meeting could be a class or a chat with friends. Label those windows, or add a routine.
- **Knowledge graph scope**: only items with no rule match, or a rule confidence below 0.6, go through the graph. Items the rules already know aren't stored there unless you correct them.
- **YouTube channel names** come from YouTube's public oEmbed endpoint, which receives only the video id. Turn this off with `youtube_channel_lookup: false`; without it, videos use title similarity and the LLM.
- **iOS** (later) only needs a collector that POSTs the same heartbeat JSON with `source: ios` and its own token.
