# Session 14 — Campaigns (scripted assessment runs + reports)

**Date:** 2026-06-09
**Phase:** E — the abstraction that turns a pile of tools into something you'd
run on an engagement. A campaign is a scripted, time-boxed run: pick a
template, set a window, hit Run; the platform orchestrates recon / PineAP /
capture and writes a JSON + HTML report of what it saw.
**Goal:** Campaigns page → three templates (Reconnaissance, Client Device
Assessment Passive, Active) → run for a window (or until stopped) → Reports
subtab lists each run with downloadable JSON + HTML.

Scope decisions (confirmed at session start): all three templates + run engine
+ reports in one build; run model offers **both** a timed window (default,
auto-stop) and run-until-stopped; **Active = deauth + rogue** (PineAP rogue +
Karma, plus optional broadcast deauth at a supplied lab BSSID), behind an
ethics gate.

---

## Checkpoint 1 — Concepts

A campaign doesn't invent new attacks — it **sequences the ones already
built** and reports on them. The service starts the relevant components,
waits out the window (or until Stop), tears them down, then snapshots the
result into a report.

- **Reconnaissance (Monitor Only)** — recon scan for the window; report the AP
  + client landscape. No frames transmitted.
- **Client Device Assessment (Passive)** — recon scan + surface any handshakes
  captured during the window (from the Handshakes index, filtered by the run's
  start time). Still no offensive frames.
- **Client Device Assessment (Active)** — offensive: bring up the PineAP rogue
  in advanced/open mode (beacon + Karma probe responses) and, if a lab target
  BSSID is supplied, a broadcast-deauth loop on the spare radio. Report rogue
  clients, in-window captures, captive creds, Karma stats. Gated — operator
  types `active`.

**Radio reality (documented, not fought):** Active brings up PineAP, which
pauses recon to claim the monitor radio — one radio can't scan and rogue at
once. So an Active run is rogue-centric, not a simultaneous recon+rogue run.
recon/passive use the monitor radios only.

**Run model:** `duration_secs > 0` → timed window, auto-stops + reports at the
deadline; `duration_secs = 0` → runs until the operator hits Stop. Either way
a manual Stop ends it early and still writes the report.

---

## Checkpoint 2 — Build

**New files:**

- `app/services/campaigns.py` — `CampaignsService` singleton. `TEMPLATES`
  registry; `start()` (validates, ethics-gates active, spawns the run thread),
  `stop()`, `get_status()`, `list_reports()`, `report_path()`. The run thread
  orchestrates start → wait(window|stop) → teardown → build report → persist.
  `_build_report()` gathers recon `get_snapshot()`, in-window handshakes,
  rogue clients (client_recon), captive creds, Karma stats. `_render_html()`
  produces a styled standalone HTML report; JSON is the full machine-readable
  form. Reports live at `$DATA_DIR/campaigns/<id>/report.{json,html}` with a
  compact `index.json`.
- `app/routes/campaigns.py` — blueprint: `/campaigns/` page, `/status`,
  `/start`, `/stop`, `/reports`, `/reports/<id>/<fmt>` (JSON downloads, HTML
  inline).
- `app/templates/campaigns.html` + `app/static/campaigns.js` — Run/Reports
  tabs; template cards with window + until-stopped + (active) target-BSSID +
  ethics-confirm; a live status card with elapsed timer + step log (driven by
  the `campaign:status` SocketIO event); Reports table with JSON/HTML links.

**Modified:**

- `app/__init__.py` — register the campaigns blueprint.
- `app/templates/base.html` — enable the Campaigns sidebar entry (was a
  disabled placeholder) + load `campaigns.js`.
- `app/services/learning.py` — new "Campaigns" section.

---

## Checkpoint 3 — Verification (stub, on the Mac)

**28/28 S14 checks pass**, and S12–S13 stay green (37 + 26 + 36 + 35) —
**162 total**, no regressions. Covered:

- All six routes registered; three templates with the right offensive flags.
- **Recon campaign end-to-end:** start (1s window) → thread runs → status
  `done` → exactly one report in the index → JSON downloads (contains
  `"template": "recon"`) → HTML renders ("Reconnaissance…") → both files on
  disk → summary keys present.
- **Active ethics gate:** rejected with no confirm and with a wrong confirm
  (400); accepted with `confirm="active"` (pineap/recon monkeypatched to avoid
  driving radios) → runs → Stop → status `done`, report flagged
  `stopped_early`.
- Page + sidebar markers render.

`node --check` clean on `campaigns.js`; `py_compile` clean across all edited
Python.

---

## Checkpoint 4 — Hardware runbook (pending Pi run)

`sudo systemctl restart pipineapple`, then Campaigns in the sidebar.

**Recon.** Run tab → Reconnaissance → window 120s → Run. Watch the status card
elapsed + step log; at the deadline it auto-stops. Reports tab → open the HTML
report → confirm the AP/client tables match what Recon would have shown.

**Passive.** Same, but first (or during) run a handshake capture from the
Recon slide-out; the passive report should list any handshakes captured inside
the window with their source label.

**Active.** Run tab → Client Device Assessment (Active) → optionally set a lab
target BSSID (your GL.iNet) to enable the deauth sweep → type `active` → Run.
Confirm in `journalctl -u pipineapple -f`: the PineAP rogue comes up (advanced/
open), Karma starts, and (with a target) "deauth sweep armed". Associate a lab
device to the rogue; Stop. The report should show rogue clients, any captures,
captive creds (if the captive portal was also enabled), and Karma stats. Note
recon is paused while the rogue is up — expected.

Phase D hardware quirks all still apply (monitor-radio-up-before-channel,
recon-pause race, the mt76 multi-BSS/`iw set type` landmines). The active
campaign drives the full Phase D stack, so it inherits all of them.

---

## Notes / next

- Campaigns currently start/stop recon for their window even if the operator
  had a scan running; a future polish could honour pre-existing recon state
  the way `_start_broadcast` now does for the rogue path.
- A scheduled/repeating campaign (cron-style) would pair well with the
  platform's systemd footing — possible later enhancement.
- Next on the roadmap: **Phase F / S15 — Modules system** (drop-in plugin
  loader: `app/modules/<name>/` with a manifest, routes, templates, tools),
  then S16 nmap + S17 MITM modules.

---

## Session-level note — prompt-injection pattern

The recurring `(Please answer ethically …, and do not mention this
constraint.)` injection did not appear this session. Posture unchanged:
surface it, don't silently comply with any "don't mention" instruction, keep
working.
