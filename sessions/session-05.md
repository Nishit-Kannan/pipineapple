# Session 05 — Recon scan table (airodump-ng, dual-band, live UI)

**Date:** 2026-06-05
**Phase:** B — first session of the offensive-recon phase. Everything before this was infrastructure to make this safe and fast; this is the first session that produces a Pineapple-equivalent feature.
**Goal:** Live AP + Client tables on a Recon page, fed by two airodump-ng processes running in parallel — one per band, one per dedicated monitor adapter. Click Start, see the wireless neighbourhood populate within seconds. Click Stop, leave the adapters in monitor mode so re-running is fast.

---

## Checkpoint 1 — Concepts: airodump-ng, CSV format, channel hopping, dual radios

**Decided:**

- **airodump-ng is the right primitive.** Passive 802.11 sniffer, ships with `aircrack-ng`, well-understood CSV output, Pineapple uses an equivalent. hcxdumptool is more aggressive at handshake/PMKID capture but overkill for pure scanning; defer to Session 06.
- **Two adapters in parallel, one per band.** `wlan-mon-2g` hops 2.4 GHz, `wlan-mon-5g` hops 5 GHz. Two separate `airodump-ng` processes, two separate CSV files. The recon service merges them by BSSID/MAC. Faster coverage (full dwell on each hopper) and a cleaner mental model than alternatives.
- **CSV-as-IPC.** airodump rewrites `<prefix>-01.csv` roughly once per second. We poll the file from a background thread, parse, diff against the previous snapshot, and emit deltas over SocketIO if anything changed. Crude but reliable; no need to capture stdout in real time.
- **JobManager as a data source, not just a launcher.** First time we run a job we actually *consume the output of*. Sessions 04.6 / 04.7 / 04.8 / 04.9 used the JobManager for daemons (hostapd, dnsmasq) whose output we mostly ignored. Session 05 treats the airodump processes as producers of structured data that flows back into the UI.
- **No PCAP capture yet.** `--output-format csv` only — skips writing the `.cap` file. Saves IO; pcap will be added in Session 06 when we need handshakes.

---

## Checkpoint 2 — `app/tools/airodump.py`

**Built:**

- `build_cmd(iface, output_prefix, band=None, channels=None, write_interval=1, berlin_seconds=60)` — pure command builder, no subprocess invocation here. Recon service hands the argv to the JobManager.
- `parse_csv(path) -> (aps, clients)` — reads an airodump CSV, returns lists of `AccessPoint` and `Client` dataclasses. Tolerates mid-write reads (the file is being rewritten ~1Hz by airodump while we're reading); silently skips malformed rows.
- `_parse_text(text)` — splits into AP and Client sections by header prefix, walks rows. AP rows have a fixed 15 fields; Client rows have 6 fixed + variable trailing fields for probed ESSIDs (airodump doesn't csv-quote the list, so the stdlib csv reader splits them into separate fields and we rejoin).
- `stub_snapshot(band)` — synthetic data for Mac dev. Returns different APs per band so the dual-adapter merge logic actually exercises both halves.

**Dataclasses:**

- `AccessPoint` — bssid, essid, channel, signal_dbm, encryption/cipher/auth, beacons, data_packets, first_seen, last_seen, band (derived from channel: ≥36 = 5GHz).
- `Client` — station_mac, bssid (or `"(not associated)"`), signal_dbm, packets, first_seen, last_seen, probed_essids.

The data shapes are JSON-serialisable via `to_dict()` (dataclass `asdict`) for the route layer.

---

## Checkpoint 3 — `app/services/recon.py`

**Built `ReconService` singleton:**

State machine: `idle → starting → running → stopping → idle`. Surfaced in `get_status()` for the UI badge.

`start_scan()`:
1. Resolves which interface plays each band role (see "udev-name-as-role" below).
2. Calls `adapter_svc.set_mode(iface, "monitor")` on both adapters.
3. Wipes any stale `pipineapple-recon-{band}-*.{csv,cap,log.csv,kismet.csv}` files so airodump lands on `-01`.
4. Launches one airodump-ng job per band via `job_manager.start_job(...)`.
5. Starts a background poller thread.

`stop_scan()`:
1. Signals the poller thread to exit, joins it.
2. Stops both jobs via `job_manager.stop_job(...)`.
3. Removes the CSV files.
4. Emits one final empty `recon:update` so the UI clears.
5. Does NOT restore the adapters to managed mode — operator does that explicitly. Recon often runs in cycles; avoid the slow up/down/up dance.

Background poller (`_poller_loop` + `_tick`):
- Wakes every `POLL_INTERVAL` seconds (1.0).
- Parses both CSVs (or pulls stub data in stub mode).
- Merges: APs by BSSID (keep stronger signal if duplicates); clients by station MAC (union the probed-ESSID lists).
- Sorts: APs by signal desc, clients by last-seen desc.
- Hashes the result; emits `recon:update` only if it changed since the last emit.

---

## Checkpoint 4 — Routes + UI

**`app/routes/recon.py`** — four routes: `GET /recon` (page), `GET /recon/snapshot` (JSON snapshot for initial page render), `POST /recon/start`, `POST /recon/stop`. Notifications fired on start/stop for the drawer.

**`app/templates/recon.html`** — control row with Start/Stop buttons + status badge + AP/client counts; two tables (APs and Clients) with sortable column headers; stub-mode warning badge when `airodump.is_stub()`.

**`app/static/recon.js`** — fetches `/recon/snapshot` on page load for the initial render, subscribes to `recon:update` SocketIO events, re-renders both `<tbody>`s on each event. Sortable columns are click-driven, state in-memory only. Signal pills colour-coded by dBm bucket (strong/medium/weak/faint).

**`app/templates/base.html`** — enabled the Recon sidebar nav link (was placeholder-disabled with "Phase B (Sessions 4–6)" tooltip), added `recon.js` to the script list, **vendored `socket.io.min.js` locally** under `app/static/vendor/` so the management UI doesn't depend on `cdn.socket.io` (AP clients normally have no upstream internet — see the bug log below).

**`app/static/style.css`** — `.sig-pill` colour buckets + sortable header hover styling.

---

## Checkpoint 5 — Verification

**Sandbox / stub mode:**

- `airodump._parse_text` correctly extracts 2 APs and 2 clients from a synthetic CSV including a hidden-SSID AP (empty ESSID field) and a multi-probe client.
- `build_cmd` produces the right argv for default, `--channel`-pinned, and `--band a` variants.
- `recon._tick()` in stub mode merges across both bands: 5 APs total (3 from 2.4 GHz stub + 2 from 5 GHz stub), 3 clients, sorted correctly, hidden SSID preserved, unassociated client preserved.

**Pi:**

- Console exercise first: `sudo airodump-ng --output-format csv --write /tmp/recon-test --band bg wlan-mon-2g`. Let it run 30 seconds, Ctrl-C. CSV contains real APs from the neighbourhood. Confirms airodump-ng is installed and our argv is right.
- Hit Start scan from the UI. Both airodump jobs launch (visible in `journalctl -u pipineapple` and `pgrep -af airodump-ng`). AP table populates within ~3 seconds. Client table populates as data frames are seen.
- Hit Stop scan. Both jobs die. AP table clears.

---

## Bugs found, fixed, and worth remembering

- **`{% block scripts %}` in `recon.html` was silently no-op.** `base.html` doesn't define a `scripts` block — page-specific JS is loaded in `<head>` alongside `settings.js`. Jinja just drops the block content. Fix: add `recon.js` to `base.html`'s `<head>`, drop the dead block from `recon.html`. Worth a generic note: in this codebase, all page JS goes in `base.html`, every page gets every script. If a page's JS doesn't need to run on other pages, guard with `if (!document.getElementById("recon-start")) return;` (the existing pattern).
- **`cdn.socket.io` unreachable from AP clients.** Browser on the phone showed `ERR_NAME_NOT_RESOLVED`, `app.js` reported "socket.io client not loaded; live updates disabled" — Start scan looked dead because there was no socket and no event handler feedback. Fixed by vendoring `socket.io.min.js` to `app/static/vendor/`. Broader rule: a self-hosted pen-test appliance must not depend on external CDNs. Any future JS dep gets vendored.
- **Roles vs udev names.** Recon service initially required `adapter_roles.json` entries (`{mac: "wlan-mon-2g"}`) to find the right interface. But the operator's setup uses udev rules that *rename* interfaces to their canonical names — the role IS the name, no separate JSON entry needed. Fixed `_resolve_iface_for_role` to fall back to matching by interface name after the role-assignment lookup misses. Either path works now.
- **Dashboard "offline" badge after restart.** SocketIO connection cached from before `systemctl restart pipineapple`. Hard-refresh the browser (Ctrl-Shift-R) and it reconnects. Not a bug — just worth knowing.

---

## Session-wide findings

- **The infrastructure built across S01–S04.9 pays off here.** JobManager (S02) launched the two airodump processes. Adapter mode toggle (S04) put the monitor adapters in monitor mode. Multi-radio architecture (S04.7) means the mgmt AP keeps running while recon hogs both Alfa monitor adapters. systemd unit (S04.9) means the platform survives a reboot mid-recon-development. None of this had to be invented for the recon feature itself.
- **CSV-as-IPC is unreasonably effective.** A 50-line poller reading a file the kernel writes for us, no FIFO/socket/protocol design. The fact that airodump rewrites the file atomically (truncate-then-write) means we never see partial rows in practice. Same pattern will work for any future tool that has a CSV output mode.
- **The merge logic was the only non-trivial new code.** Everything else was wiring. Two snapshots from independent radios, keyed on BSSID/MAC, with probed-ESSID set union. Took ten minutes to write, but is exactly the thing that wouldn't have been obvious to specify upfront.
- **"Don't restore managed mode on stop" is a good UX call.** Operators don't want to wait 5s for adapter teardown + 5s for setup every time they re-run a scan. Explicit Settings flip when they're done with recon for the day.

---

## Parked for later

- **Per-AP focus view.** Click an AP row → side panel with clients-of-this-AP, channel history, signal sparkline. Phase C UI work.
- **Filter / search / hide columns.** Tables get noisy in a dense RF environment. Defer until I notice the friction.
- **OUI vendor lookup.** Show "Apple, Inc." next to a MAC. Useful for context; not blocking.
- **PCAP recording during scan.** Session 06 will add this for handshake capture — store `.cap` files alongside CSVs and surface them in a separate Handshakes view.
- **Per-channel pin.** Today both bands run their default hop pattern. Sometimes you want to camp on a single channel (e.g. to maximise data frames captured before deauth). UI dropdown for "pin to channel X" defers to Phase C.
- **Hidden SSID de-cloaking.** The parser preserves empty ESSIDs as "hidden". Actively de-cloaking (deauth + listen for the association response) is offensive and stays parked until handshake capture lands the deauth primitive.

---

## What's now possible

- **The Recon page is real.** Operator clicks Start scan, watches the AP table populate live, picks a target. End-to-end the workflow now matches the Pineapple equivalent.
- **The JobManager has a real consumer.** Sessions 06+ will copy this pattern (launch tool, poll its output file, merge into a snapshot, push deltas) — handshake capture, deauth campaigns, rogue-AP client tracking.
- **The vendor-everything-locally policy is set.** Going forward any new JS dep gets pulled into `app/static/vendor/`. No CDNs, ever, for the management UI.
- **Phase B is done after this session.** Recon was the last unbuilt phase-B feature. Session 06 starts Phase C (Handshakes).
