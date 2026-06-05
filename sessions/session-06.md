# Session 06 — Recon slide-out: beacon parsing, probe history, deauth

**Date:** 2026-06-05
**Phase:** B — feature deepening on top of the recon scan table from S05. First session that consumes scapy and that injects frames (deauth). Sets up the handshake-capture flow that lands next in S07.
**Goal:** Click an AP row in the Recon table → right-side slide-out with parsed beacon Information Elements (RSN cipher/AKM, HT/VHT/HE flags, vendor IEs, country code) + a Security tab that surfaces WPA2/WPA3 + MFP-required hints + a Deauth All Clients action button gated behind an ethics confirm modal. Click a Client row → same slide-out shape with a Probe History tab showing per-(client, SSID) timing, broadcast-vs-directed flag, and an "in range vs not in range (PNL leak)" classification.

---

## Checkpoint 1 — Concepts: beacon IEs, RSN, deauth, slide-out pattern

**Decided:**

- **Pcap output added to recon, parsed on demand.** airodump-ng's CSV only summarises a few fields per AP — for the slide-out's Tagged Parameters viewer we need the full beacon body. Switched recon to `--output-format csv,pcap` so the pcap is always available; slide-out backend reads the pcap and parses the most recent beacon for the focused BSSID via scapy.
- **scapy as a dependency.** Late-imported in `app/tools/beacon_parser.py` so the recon service doesn't pay the (heavy) load cost on every poller tick. Falls back gracefully if scapy isn't installed (returns None / empty list, slide-out shows a hint).
- **Probe-request aggregation per (client, SSID).** Same scapy parse loop walks Dot11ProbeReq frames and aggregates first_seen / last_seen / count / broadcast-vs-directed. Foundation for the Karma-style impersonation pool in Phase D.
- **Deauth via aireplay-ng on the injection radio.** Operator's `wlan-ap` (third Alfa, MT76 chipset) is used as the injection radio so the two recon adapters stay listening on their hop pattern. Standard pattern: aircrack-ng's `--deauth N` sends N bursts of deauth frames; broadcast destination kicks all clients, `-c <CLIENT_MAC>` targets one.
- **Slide-out as a reusable pattern.** Right-side panel with header + tabs + body + actions. Two consumers in this session (AP detail, Client detail), more to come (Handshakes S08, Modules later). Closes via × button, ESC, or backdrop click.
- **Ethics gate explicit.** Deauth is offensive. Even though the platform's whole purpose is offensive WiFi work, the UI surfaces a "type the word deauth to confirm" modal before any frame goes out. Lab equipment only, every time.
- **MFP-required → deauth disabled.** When the parsed RSN shows MFPR=1 (WPA3-Personal mandates this; WPA2 has it as an option), the deauth button auto-disables with a tooltip — the target's clients are using protected management frames and our spoofed deauth will be cryptographically rejected. Avoid the operator wasting time trying.

---

## Checkpoint 2 — Wrappers + service additions

**Built:**

- `app/tools/beacon_parser.py` — `parse_latest_beacon(pcap, bssid)` walks the pcap and keeps the last beacon whose BSSID matches; returns parsed RSN (cipher/AKM/MFP), HT/VHT/HE flags, country code, vendor IE list, and raw tagged-parameter dump. `parse_probe_requests(pcap)` aggregates probe frames by (mac, ssid) with timing/count. Both cached for 5s on (path, mtime) — see the bug list below for why this matters.
- `app/tools/aireplay.py` — `send_deauth(iface, bssid, client_mac=None, count=10)`. MAC validation, count bounds (1-1000), stub-mode safe. Caller responsible for monitor mode + channel pinning.
- `app/tools/iw.py` — `set_channel(iface, channel)` for pinning the injection radio. Plus `recreate_interface(iface)` (the Realtek nl80211 reset — see bug list).
- `app/services/recon.py`:
    - `get_ap_detail(bssid)` — merges snapshot AP record + parsed beacon IEs from the right band's pcap + currently-associated clients.
    - `get_client_detail(mac)` — merges snapshot client record + full probe history from both pcaps with cross-band merge.
    - `deauth_ap(bssid, client_mac, count)` — full orchestration: resolve injection iface, drop NM, monitor mode, pin channel, fire aireplay.
    - `_tick` enrichment — adds `ap_ssid` (BSSID→SSID lookup), `probed_in_range` (subset of probed SSIDs matching a visible AP), `probed_not_in_range` (the privacy-interesting ones).
- `app/routes/recon.py` — `GET /recon/ap/<bssid>/detail`, `GET /recon/client/<mac>/detail`, `POST /recon/ap/<bssid>/deauth`.
- `app/templates/recon.html` — slide-out structure + ethics-confirm modal. Clients table reworked: SSID prominent with BSSID muted subtitle; probed-for SSIDs that match an in-range AP get info-badged.
- `app/static/recon.js` — row click → fetch detail → render slide-out tabs (AP: Overview / Security / Tagged params / Clients; Client: Overview / Probes). Sort, tab switching, ESC + backdrop close. Ethics modal with "type deauth to confirm" input gate. Auto-disables deauth button when MFP is required. Live scan duration ticker.
- `app/static/style.css` — `.slideout` (right-side panel + transitions), `.slideout-backdrop`, `.modal` (centered card + dimmed background), `.slideout-tab.active`, `.ie-table`, clickable-row hover.

---

## Checkpoint 3 — Verification on Pi

**Functional:**

- Start scan → AP and Client tables populate live.
- Click AP row → slide-out opens, Overview tab shows snapshot fields, Security tab decodes RSN to "WPA2-Personal / WPA3-Personal / WPA2-Enterprise" + MFP flags, Tagged Parameters tab shows the raw IE dump.
- Click Client row → slide-out shows probe history. iPhones with MAC randomization appear as multiple "clients" (one per saved SSID), which is correct behavior.
- Deauth button → modal opens, requires typing `deauth`, fires aireplay-ng on `wlan-ap`, client briefly disassociates from the AP. Successful kick visible in `journalctl` and via the AP-side disassociation event.
- SSID reconciliation works — clients in the table show their AP's name (e.g. "HomeWiFi") instead of just the AP's BSSID.
- "Probed SSIDs — NOT in range (PNL leak)" section surfaces stale networks the device remembers. Useful for personal PNL hygiene.

**Live scan duration ticker** in the control row updates every second while state is `running`. Format `running 02m 14s`.

---

## Bugs found, fixed, and worth remembering

This session's debugging trail was the longest of the project so far. Documenting in full because every one of these is something that will recur in some form for future sessions.

### A. UI bugs

- **`{% block scripts %}` in recon.html was silently no-op.** `base.html` has no `scripts` block; page-specific JS is loaded in `<head>` alongside `settings.js`. Jinja just drops content for an undefined block. Fix: add `recon.js` to `base.html`'s `<head>`, drop the dead block from `recon.html`. General rule for this codebase: all page JS goes in `base.html`, every page gets every script, guard at the top of each handler with `if (!document.getElementById("recon-start")) return;`.

- **`cdn.socket.io` unreachable from AP clients.** Browser on the phone showed `ERR_NAME_NOT_RESOLVED`, `app.js` reported "socket.io client not loaded; live updates disabled" — Start scan looked dead because there was no socket and no event handler feedback. Fixed by vendoring `socket.io.min.js` to `app/static/vendor/`. Broader rule: a self-hosted pen-test appliance must not depend on external CDNs. Any future JS dep gets vendored.

- **Slide-out was transparent and close button invisible** (during initial implementation). Used CSS variables `--bg-elev-1` and `--bg-elev-0` that don't exist (actual variable is `--bg-elev` with no number). CSS falls back to transparent on undefined `var()`. Fix: use the actual variables. Close button beefed up too — "Close ×" with explicit border and red hover, plus ESC and backdrop click to dismiss.

- **`iw dev` parser leaked P2P-device into the previous interface.** When `iw dev` output included an "Unnamed/non-netdev interface" stanza (the brcmfmac auto-spawned P2P-device alongside wlan0), our parser regex matched only `Interface NAME` lines and silently appended the subsequent `type P2P-device` field to the previous named interface (wlan-mon-2g). Settings → Adapter Management table then showed wlan-mon-2g as `p2p-device` mode after a stop_scan. Fix: also treat lines starting with `phy#` or `Unnamed` as section breaks that finalise the current interface.

### B. Service / orchestration bugs

- **NetworkingService wasn't a singleton.** `get_service()` was returning a fresh instance every call. NetworkingService stores live JobManager job IDs on the instance (`_mgmt_hostapd_job_id`, `_mgmt_dnsmasq_job_id`); without a singleton, every request handler got a fresh instance with `None` job IDs, so the disable path's `if jid: stop_job(jid)` silently no-op'd. Result: AP reconfigure tried to start new dnsmasq while the old one was still running, failing with "Address already in use" on port 53. Fix: module-level singleton, same pattern as ReconService. AdapterService and AuthService don't need this because they store all state in JSON, not on the instance.

- **Async work in Flask needs an app_context.** Background threads spawned outside a request (recon teardown thread; networking restore thread) don't inherit Flask's request context. Anything that calls `current_app` raises RuntimeError, the exception is swallowed by the thread runner, and the work never completes. Symptom: recon teardown never reached the "set state to IDLE" line, UI badge stuck at "stopping" forever. Fix: capture `current_app._get_current_object()` before spawning, then `with app.app_context(): ...` inside the thread.

- **stop_scan was synchronous and blocked the HTTP request.** With pcap output running, airodump's SIGINT cleanup (flush buffers, reset radio) takes meaningful time; full teardown for two adapters was 10+ seconds. Browser sat there frozen during the wait. Fix: spawn teardown in a daemon thread, return immediately with `state: stopping`. JS polls `/recon/snapshot` every 2s as a fallback if the final SocketIO emit doesn't land.

- **Duration ticker kept ticking through "stopping" state.** My initial condition was "tick when state != idle"; should have been "tick only when state == running". Transient `starting` / `stopping` aren't actual scan time.

### C. Driver bugs (the deep ones)

- **Realtek `rtw_8821cu` requires a netdev reset between hostapd runs.** AP reconfigure (operator changes SSID and clicks Apply) consistently failed with:
  ```
  nl80211: kernel reports: Match already configured
  nl80211: Could not configure driver mode
  wlan-mgmt-ap: AP-DISABLED
  hostapd_free_hapd_data: Interface wasn't started
  ```
  The standard `ip link down` → `iw set type managed` → `ip link up` dance isn't sufficient for the Realtek out-of-tree driver — internal per-interface nl80211 vif state persists. Only reliable fix: destroy and recreate the netdev via `iw dev <iface> del` + `iw phy phyN interface add <iface> type managed`. Wrapped as `iw.recreate_interface(iface)`, called from `_disable_mgmt_ap_unlocked`. Safe on mt76 chipsets too (no driver-specific behavior).

- **dnsmasq port-53 race on AP restart.** SIGTERM → process exits, but the kernel can hold the socket briefly. Next dnsmasq starts immediately and fails with "Address already in use". Already fixed for the internet-sharing toggle path in S04.9 via `_wait_for_port_free`; needed the same fix in `_enable_mgmt_ap_unlocked` for the AP reconfigure path. Added.

- **SIGINT, not SIGTERM, for aircrack-ng tools.** airodump-ng installs a SIGINT handler that flushes CSV + pcap and tears down the channel hopper cleanly. SIGTERM bypasses that handler and leaves the mt76 driver in a state where the next op (or just idleness) can kernel-hang the USB controller — which on Pi 5 also serves the SSD, locking the whole machine. Two Pi hard-hangs during S06 testing traced to this. Fix: JobManager.stop_job grew a `first_signal` parameter, recon passes `signal.SIGINT`.

### D. The 4 GB log files (the worst one)

The headline performance bug of the session, discovered last because the symptom was misleading.

- **Symptom evolution:** First test, stop scan locked up the Pi (SSH dropped, hard reboot required). After adding SIGINT + driver reset, stop scan stopped locking but the Pi got "very slow" afterward. Initial theory: scapy parsing a large pcap on slide-out fetch. Wrong — slide-outs weren't even open at stop time.

- **Actual cause:** `JobManager.start_job(stdout_path=f"/tmp/pipineapple-recon-{band}.log")` captured airodump-ng's stdout to disk. airodump's stdout is the live curses-style table — full redraw with ANSI escape codes every `--write-interval` second. After 5 minutes of scan, each log file was **2 GB**. Two adapters = **4 GB**. `/tmp` on Pi OS is tmpfs (RAM-backed). 4 GB of RAM gone, swap thrashing, Pi grinding.

- **Fix:** Change `stdout_path` to `/dev/null` for airodump specifically. We never read this stdout anyway — useful data goes to the CSV and pcap files via `--write`. One line change, eliminates the entire bug.

- **Lesson:** When a daemon's stdout is a UI rather than structured output, capture-to-file is actively harmful. Worth grepping any other JobManager consumers for the same pattern before they hit it.

### E. Performance hygiene added preemptively

- **Parser caching.** `parse_latest_beacon` and `parse_probe_requests` cache on `(path, mtime, ...)` for 5 seconds. Opening the same slide-out twice in a row doesn't re-walk the pcap. Critical when the operator clicks around several APs / Clients fast.
- **Slow-parse warning.** Logs `"parse slow: %.1fs for %s (pcap size %d MB)"` if any single parse takes >2s. Gives breadcrumbs without crashing things.
- **Pcap size guardrails.** Recon poller emits a yellow notification when either pcap crosses 30 MB ("slide-out opens are getting slow") and a red notification at 100 MB ("slide-out opens may freeze the Pi"). Doesn't auto-stop the scan — that'd be surprising — but the operator gets the signal.

---

## Session-wide findings

- **Hardware driver bugs eat real time.** Three of today's biggest issues (Realtek nl80211 stuck state, mt76 + airodump kill, the USB-controller-shares-SSD lock-up) were driver-level. The platform code can mitigate but not eliminate them. Worth knowing what's hardware vs software when triaging future symptoms — a Pi unresponsive over SSH is almost always a driver / kernel issue, not Python.

- **Singleton state is invisible until it breaks.** NetworkingService had been non-singleton since S04.6. Nothing failed visibly because most paths re-read JSON on every call. The bug only surfaced once we tried to *stop* a daemon via a different request from the one that *started* it. Audit takeaway: any service that stores live process state on instance fields must be a singleton. AdapterService and AuthService are fine (JSON-backed). NetworkingService and ReconService both need the pattern.

- **`/dev/null` is not the default but it should be.** JobManager defaults stdout to a pipe → reader thread → SocketIO room. With `stdout_path`, it captures to a file (originally intended for "I want post-mortem logs"). Neither default is right for "the daemon's stdout is noise we should discard." Adding a `discard` mode to JobManager is on the to-do list.

- **Persistent journald is now non-optional.** Pi OS Lite's default volatile journal cost us full diagnostic data for two of today's lock-ups. Enabled persistent journald during the session; should be in the boot setup checklist going forward.

- **Async + Flask context is a trap.** This is the second time we've hit it (first was the networking restore thread in S04.7). Worth a project-wide convention: any thread spawned outside a request handler should push an app_context as its first action, in a `try` with the actual work as the body. Add a small utility helper for this in S07+.

---

## Parked for later

- **OUI vendor lookup** — show "Apple, Inc." next to a MAC in the Clients table. Useful but not blocking; standalone tool (`ieee-data` Python package or local OUI database).
- **Per-AP focus view** — clicking an AP currently opens the slide-out; eventually we want a full "focus mode" that filters Clients table to just that AP's clients. Phase C UI work.
- **Probe-request filter / search** — once a busy environment is producing hundreds of probes, the table needs filtering. Defer until the data volume warrants it.
- **`(deprecated) wireless extensions` warning** — kernel logs "python uses wireless extensions which will stop working for Wi-Fi 7 hardware; use nl80211" because something we call uses the old WEXT ioctl interface instead of nl80211. Cosmetic for now; track down before Wi-Fi 7 hardware shows up.
- **Auto-restart airodump when pcap exceeds N MB** — we warn but don't act. Eventually want to rotate pcaps so long scans don't accumulate.
- **Per-client deauth from the Client slide-out** — Session 07 (handshake capture) will likely need this; the wrapper already supports `client_mac=`, just no UI yet.

---

## What's now possible

- **The full Pineapple-style Recon experience.** Live scan tables + click-through detail panels + per-target actions (deauth) + privacy-leak surfacing (PNL probes). The Recon page covers everything we'd want from a wireless reconnaissance tool short of handshake capture itself.
- **Session 07 (handshake capture) is unblocked.** All the prerequisites are in place: pcap output is being written, scapy parse infrastructure exists, deauth is wired up, the injection radio orchestration pattern is established. S07 can focus on the EAPOL-watcher logic.
- **Plumbing for Phase D (PineAP) is mostly built.** The probe-request aggregation that S06 added is exactly what the "SSID impersonation pool" needs in S10. The ethics-confirm modal pattern reuses. The slide-out reuses for the Clients view.
- **The deploy pipeline is hardened.** Today's bug list will not recur on the same paths: persistent state via systemd + `/var/lib/pipineapple`, `PYTHONDONTWRITEBYTECODE` in the unit, singleton services, app-context-wrapped threads, stdout to /dev/null for airodump, idempotent set_managed, port-53 wait. Future sessions inherit all of this.
