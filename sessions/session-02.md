# Session 02 — Realtime layer + Notifications + JobManager

**Date:** 2026-06-04
**Phase:** A — Dashboard & chrome (continued)
**Goal:** Make the dashboard live (WebSocket-driven updates), wire a real Notifications system with the five Pineapple severity levels, lay the JobManager foundation that every later session's long-running tools will use. Swap the Unicode-glyph sidebar icons for real inline SVG.

Journal format unchanged from Session 01 — incremental checkpoints added in real time, each one capturing what was built, the decisions, and the findings.

---

## Checkpoint 1 — Concepts: Flask-SocketIO, async modes, JobManager pattern, severity levels

**Decided:**

- **WebSockets via Flask-SocketIO**, not polling or SSE. Bidirectional channel is needed for JobManager's later "subscribe to job N's stdout" + "kill job M" flows; SSE would force a second protocol for client → server, polling would be wasteful.
- **`async_mode="threading"`** — Flask-SocketIO's built-in threading backend. Concurrency cap (dozens of clients) is way above what a single-user lab needs, and we avoid eventlet's monkey-patching surprises. `eventlet`/`gevent` stay available as a `production` extra for Session 19's nginx deployment.
- **JobManager pattern** for every long-running subprocess: centralised lifecycle (start/stop/list), bounded stdout buffer per job (deque maxlen=500), per-job SocketIO room for streaming.
- **Five notification severities** matching the Pineapple exactly: info / warning / error / success / unknown. Only warning + error + success light up the bell-dot indicator — info would create constant noise from routine events.

---

## Checkpoint 2 — SocketIO scaffold + live dashboard

**Built:**

- Module-level `socketio = SocketIO(async_mode="threading", ...)` singleton in `app/__init__.py`. Factory now calls `socketio.init_app(app)` after Flask is set up.
- `run.py` switches from `app.run()` to `socketio.run(app, ..., allow_unsafe_werkzeug=True)`. The `allow_unsafe_werkzeug=True` is required by Flask-SocketIO when running the werkzeug dev server (its way of making sure you don't accidentally ship it as prod).
- `app/services/sysinfo_broadcaster.py` — daemon thread that emits the `sysinfo` event every 2 seconds with the full status dict. Started from the factory's `_start_background_tasks()`. Guarded against the Flask-debug reloader's parent process via `WERKZEUG_RUN_MAIN`.
- `app/static/app.js` — front-end glue. Connects via `io({ transports: ["websocket", "polling"] })`, subscribes to `sysinfo` / `notification` / `notification:read_all` / `notification:clear` events, updates the dashboard stat cards and tables in place using `data-field` attributes.
- Title bar gains a **live indicator** (small pill next to brand): green pulsing dot + "live" when WS is up, grey "offline" when down. State driven by SocketIO's `connect`/`disconnect`/`connect_error` events.
- Dashboard template's stat cards and table bodies are tagged with `data-field` attributes so the JS can find and update them without rebuilding the page.
- Wireless/interface tables now use Jinja partials (`_wireless_rows.html`, `_interface_rows.html`) for initial server render; the JS rebuilds the same row HTML on each `sysinfo` event.

**Decision worth noting:** stdout-render strategy. The simplest live-update path would have been Jinja-only initial render + JS that does no DOM diffing (rebuild entire tbody innerHTML on each event). That's what we shipped. Two reasons: tables are small (≤10 rows), and DOM diffing adds dep weight. If tables grow into the hundreds (large recon scans in Session 05), revisit.

---

## Checkpoint 3 — Notifications service + drawer UI

**Built:**

- `app/services/notifications.py` — `NotificationsService` singleton with `info()` / `warning()` / `error()` / `success()` / `unknown()` methods, in-memory deque (maxlen=50) of entries (id, severity, message, source, ts, read). `attach_socketio()` wires it to the realtime layer so every `add()` emits a `notification` event.
- `unread_count(loud_only=True)` — only counts warning + error + success when `loud_only` is True. This is what drives the bell-dot indicator visibility.
- Title bar bell button now has a real `#notif-dot` element styled with a subtle pulse animation. Clicking the bell opens the drawer; clicking outside closes it.
- Notifications drawer in `base.html`: `360px` fixed-position panel beneath the title bar. Header has Mark all read / Clear actions; list shows the most recent first with severity badge, message, source, relative timestamp ("just now", "3m ago"). Empty state: "No notifications yet."
- `app/routes/debug.py` (registered only when DEBUG is True): `POST /debug/notify` to emit one notification, `GET /debug/notify/burst` to fire one of each severity — handy for visual review without a real event to trigger them.

---

## Checkpoint 4 — JobManager service skeleton

**Built:**

- `app/services/job_manager.py` — `JobManager` singleton with `start_job(cmd, name?, tags?)`, `stop_job(job_id, grace=2.0)`, `get_job(job_id)`, `list_jobs(tag=None)`, `stop_all()`.
- `Job` dataclass tracks id (uuid hex[:12]), name, cmd, pid, status (pending/running/completed/failed/killed enum), start/end times, exit_code, tags, and a private deque(maxlen=500) of stdout lines.
- `start_job` spawns the process with `subprocess.Popen(stdout=PIPE, stderr=STDOUT, bufsize=1, text=True)` and immediately kicks off a per-job reader thread that drains stdout line by line, appending to the buffer and emitting `job:stdout` events on a `job-<id>` SocketIO room.
- `stop_job` sends SIGTERM, waits `grace` seconds, escalates to SIGKILL on timeout. Returns `(stopped, reason)` tuple so callers can surface diagnostic info if the stop doesn't take.
- `stop_all` called at Flask shutdown will SIGTERM every running job so we don't leak child processes between Flask restarts.
- **Tests:** 5 cases in `tests/test_job_manager.py` covering completed (`echo hello`), killed (`sleep 30` with stop), failed (nonexistent binary), list + get + tag filtering, stop_all noop. Plus 5 cases for the Notifications service in `tests/test_notifications.py`. All 10 pass.

**For S02 there's no UI consumer of JobManager yet** — the first one is Session 04's background airodump. Debug routes (`/debug/job/start`, `/debug/job/list`, `/debug/job/<id>`, `/debug/job/<id>/stop`) exercise it for verification.

---

## Checkpoint 5 — Swapped Unicode-glyph icons for inline SVG

**Built:** Replaced every `📖 ⚙ 🍍 ▢ ◎ ⇄ ▶ ▥` glyph in `base.html` with stroke-based inline SVG. Sidebar: dashboard (grid), recon (radio-waves), pineap (target), handshakes (link), campaigns (play), modules (package), learning (open book), settings (gear). Title bar: bell (notifications), info circle, terminal, vertical dots (more). All `viewBox="0 0 24 24"`, `stroke="currentColor"` so they inherit the section's accent colour when active.

**Why inline rather than CDN or sprite:** offline-resilient (no external dep), no FOUC, easy to tweak per-icon. Cost: `base.html` got longer. Acceptable trade for now.

---

## Checkpoint 6 — Deploy: hit two bugs, fixed both

**Built:** First deploy of Session 02 changes to the Pi.

**Bug 1 — WebSocket handshake 500.** Symptoms: `pi-lab` Flask log spammed:

```
"GET /socket.io/?EIO=4&transport=websocket HTTP/1.1" 500 -
AssertionError: write() before start_response
```

**Root cause:** `flask-socketio` in `async_mode="threading"` runs over werkzeug's dev WSGI server. When the client tries to upgrade from long-polling to a real WebSocket, the upgrade goes through python-engineio. Engineio needs `simple-websocket` to handle the upgrade at the WSGI layer — without it, the WSGI write path explodes because the response status hasn't been set yet.

**Fix:** Added `simple-websocket>=1.0` to `pyproject.toml`'s main dependencies. After `pip install -e .` on the Pi and restart, the live indicator pill turned green and dashboard cards started ticking without page reload.

**Bug 2 — Notification dot didn't appear when unread > 0.** Symptoms: `curl http://pi-lab.local:5000/debug/notify/burst` added four notifications (one of each severity), the drawer correctly populated, but the bell icon stayed dotless.

**Root cause:** CSS specificity. The original rule `.iconbtn .badge-dot { display: none; ... }` had higher specificity than the browser's default `[hidden] { display: none }` UA rule. Even when the JS set `dot.hidden = false`, my rule's `display: none` won.

**Fix:** Removed the unconditional `display: none` from `.iconbtn .badge-dot` and added an explicit `.iconbtn .badge-dot[hidden] { display: none; }` rule that fires only when the `hidden` attribute is present. Also added a subtle `badge-pulse` animation so the indicator actually catches the eye when it shows up.

---

## Checkpoint 7 — Console exercise: subprocess inspection

**Walked through (on the Pi, against a JobManager-spawned sleep job):**

- `ps -fp <pid>` — saw the parent PID pointed at the Flask process, confirming the job is a child of the web app.
- `cat /proc/<pid>/status` — state was `S (sleeping)` for `sleep 20` most of its life; switched to `Z (zombie)` briefly between the process exiting and the reader thread calling `proc.wait()` (which reaps it). That zombie window is exactly what JobManager's reader thread closes.
- `cat /proc/<pid>/cmdline | tr '\0' ' '` — confirmed argv matches what JobManager was told to run.
- `ls -l /proc/<pid>/fd/` — saw the stdout descriptor pointing at a pipe (`pipe:[NNNN]`) — that's the other end of `subprocess.PIPE`, which the reader thread is draining. fd 2 (stderr) pointed at the same pipe because `stderr=subprocess.STDOUT` in the Popen call.
- `kill -l` — looked at the signal table. Confirmed SIGTERM=15 and SIGKILL=9.
- `kill -TERM <pid>` — sent from a separate shell to a sleep job. Within milliseconds the JobManager-emitted `job:exited` event flowed to the browser (via SocketIO), and the dashboard reflected the status change.

**Lesson worth journaling:** the zombie state between process exit and reaper is a real gotcha. If we *didn't* have the reader thread calling `proc.wait()`, jobs would linger forever as zombies. JobManager's reader-loop tail (`rc = proc.wait()`) is what prevents zombie buildup.

---

## Checkpoint 8 — Learning Centre updated for Session 02

**Added** a new top section to `app/services/learning.py`: **Subprocesses & signals**. Eleven commands covering ps variants, /proc inspection, signal sending, lsof. Each entry links back to `app/services/job_manager.py` as the wrapper module, and the section's `ui_reference` is the `/debug/job/*` endpoints.

The Learning Centre now has five topic sections — four from Session 01 (system status, network interfaces, wireless radios, driver detection) plus this new one.

---

## Checkpoint 9 — Session 02 wrap

**Shipped:**

- Flask-SocketIO integration in threading mode, real-time live dashboard.
- `Notifications` service with five severity levels, drawer UI in the title bar, bell-dot indicator.
- `JobManager` service skeleton with subprocess lifecycle management, per-job stdout streaming via SocketIO rooms.
- Real inline SVG icons across the sidebar and title bar.
- `/debug/*` routes for testing notifications and jobs without real tools.
- 10/10 unit tests across `JobManager` and `Notifications`.
- Learning Centre updated with a fifth section (Subprocesses & signals).

**Two bugs surfaced and fixed:** missing `simple-websocket` for the WS upgrade handshake, and CSS specificity hiding the notification dot.

**Parked for Session 03 (now Session 04 since 03 was folded into 01):**

- Adapter management UI — toggle monitor mode per Alfa from the Settings page, sticky udev names for `wlan-mon-2g` / `wlan-mon-5g` / `wlan-ap`.
- First real JobManager consumer: backgrounded `airodump-ng` for Session 05's recon scan.

**Parked further out:**

- Real Web Terminal (currently a stub icon in the title bar) → Session 19 polish.
- Production deployment with eventlet + gunicorn + nginx → Session 19.
- Page reload survivability — the Notifications service is in-memory; restarting Flask wipes them. Considered storing in SQLite but deferred to Session 17's persistent-state work.

---

## Session-wide findings

- **`simple-websocket` is invisible-but-required** for Flask-SocketIO in threading mode on werkzeug. The dependency isn't pulled in by `flask-socketio` itself but is required for the WebSocket upgrade handshake. Without it: HTTP 500 + `AssertionError: write() before start_response` at the WSGI layer.
- **CSS specificity beats the `hidden` attribute** unless explicitly handled. Browser default UA rule `[hidden] { display: none }` is low-specificity; class-level rules override it. If you want a JS-toggled `hidden` to work, either don't set `display` in your own rules, or add an explicit `.your-class[hidden] { display: none }` to win.
- **Reader thread is what reaps zombies.** The cleanest signal that a JobManager-spawned process has truly gone is the reader thread completing — that's when `proc.wait()` runs and the kernel removes the zombie entry. Without the reader, `subprocess.PIPE` fills (4 KB on Linux), the child blocks on stdout write, and you've got a stuck process *and* an eventual zombie.
