# Session 01 — Flask scaffold + Pineapple-shaped dashboard

**Date:** 2026-06-02
**Phase:** A — Dashboard & chrome
**Goal:** Lay the foundation for PiPineapple. Flask app factory, system status data pipeline, Pineapple-shaped dashboard chrome, first deploy to the Pi.

This session also absorbed three significant mid-flight pivots: a curriculum reset from console-first to UI-first learning (the original `wifi-pentest-lab/` work is now under `archive/`), an OS pivot from Kali to Raspberry Pi OS Lite Trixie, and a UI realignment to mirror the real Hak5 Pineapple's section taxonomy after reviewing their docs at `docs.hak5.org/wifi-pineapple/ui-overview/`.

Journal format: incremental checkpoints added in real time. Each milestone gets its own section with what was built, key decisions, findings, and anything parked for later.

---

## Checkpoint 1 — Concepts: Flask factory, three-layer architecture

**Decided:**

- **Flask app factory pattern** (`create_app()`) rather than a module-level singleton. Lets us run dev/test/mac configs side-by-side and avoids global-state pitfalls as the project grows past Session 03.
- **Three-layer architecture** with strict dependency direction: `routes/` → `services/` → `tools/`. Routes are thin (HTTP layer), services orchestrate, tools encapsulate subprocess. Tools never import services; services never import routes.
- **MacDevConfig** exists specifically so UI iteration can happen on the Mac without monitor mode. `USE_REAL_TOOLS=False` flag flips every tool wrapper between real subprocess calls and canned stub data.

**Why it matters:** these structural decisions cascade through every later session — every blueprint added is its own file under `routes/`, every shell tool gets its own module under `tools/`. Setting up the pattern right at S01 means S03+ don't have to refactor.

---

## Checkpoint 2 — Project scaffold

**Built:** `pyproject.toml`, `.gitignore`, `README.md`, empty package `__init__.py` files for `app/`, `app/routes/`, `app/services/`, `app/tools/`. Created `app/templates/`, `app/static/`, `sessions/` directories.

**Decided:**

- **pip + venv** over `uv` or Poetry. Standard library, present on every Linux install, zero extra friction.
- **Git** over rsync for syncing Mac → Pi. Permanent history is worth the small upfront cost.
- `pyproject.toml` declares two extras: `realtime` (Flask-SocketIO + eventlet, lands in Session 02) and `dev` (pytest + ruff).
- `.gitignore` aggressively excludes capture artifacts (`*.cap`, `*.pcap`, `*.pcapng`, `*.hc22000`) so no accidental commits of intercepted traffic.

---

## Checkpoint 3 — Flask app factory + initial base template

**Built:** `app/__init__.py` with the `create_app()` factory, `app/config.py` with four config classes (Dev/MacDev/Test/Prod) plus a `resolve_config()` helper, `run.py` entrypoint, `app/routes/dashboard.py` blueprint, initial `app/templates/base.html` and `dashboard.html` (later rewritten to match the Pineapple).

**Decided:**

- Factory mirrors `USE_REAL_TOOLS` into the env (`PIPINEAPPLE_USE_REAL_TOOLS`) so `app/tools/` modules can read it without importing Flask. Keeps the dependency direction strict.
- `ProdConfig.validate()` refuses to run if `SECRET_KEY` is the default — small safety net for the eventual nginx deployment.
- `DATA_DIR` defaults to `/tmp/pipineapple` so a fresh boot starts clean.

**Verified:** Factory in the sandbox — `create_app("dev")` constructs cleanly, routes register (`/ → dashboard.index`), GET `/` returns 200 with expected content.

---

## Checkpoint 4 — System info service (sysinfo)

**Built six tool wrappers** under `app/tools/`, one per external command, plus a shared `_common.py`:

- `proc.py` — reads from `/proc` and `/sys` (CPU temp, memory, uptime, kernel, Pi model, `/proc/net/wireless`).
- `vcgencmd.py` — Pi-native temperature read, falls back to `/sys/class/thermal/thermal_zone0/temp`.
- `iproute.py` — `ip -j addr show` JSON parsing for interfaces, MACs, addresses.
- `iw.py` — `iw dev` text parsing for wireless mode/channel/frequency/SSID/txpower, `iw reg get` for regulatory domain.
- `ethtool.py` — `ethtool -i <iface>` for driver name.
- `_common.py` — shared `run()` helper (centralised timeout, encoding, logging) and `stub_mode()` reader.

**Built service** `app/services/sysinfo.py` — `get_system_status()` composes the full status dict from the tools, plus `format_uptime()` and `format_bytes()` helpers for the template.

**Findings:**

- Stub mode and real mode both work cleanly when verified in the sandbox.
- Real mode gracefully degrades when tools are missing: returns `None` per field rather than crashing. In the sandbox (no `iw`, no `vcgencmd`), we still get memory + uptime + kernel from `/proc` and loopback from `ip`.
- Same function signatures regardless of stub mode — services and routes don't have to care which way it's running.

---

## Checkpoint 5 — Dashboard route + initial template

**Built:** Dashboard route wires `sysinfo.get_system_status()` to `dashboard.html`. Template renders the system block (model/kernel/CPU temp with hot/warm/ok badging/memory/uptime/reg domain), network interfaces table, wireless radios table with mode badges and signal/txpower per radio.

**Verified:** All 11 content checks pass in the test client — Pi model present, kernel string, three Alfa adapters listed with `mt76_usb` driver, monitor-mode badges, US reg domain, formatted memory (8.0 GB) and uptime (05h 03m), channel 6 (2437 MHz) and channel 36 (5180 MHz) entries, active nav state.

---

## Checkpoint 6 — OS pivot: Kali → Raspberry Pi OS Lite

**Decided:** Mid-session, switched the target OS from Kali ARM 64-bit to Raspberry Pi OS Lite 64-bit. (Ended up installing **Trixie / Debian 13**, even newer than the Bookworm I'd been planning around — Python 3.12+ instead of 3.11.)

**Rationale:**

- Pi OS Lite is a minimal base — better foundation for a long-running appliance-style service than Kali's kitchen-sink desktop distro.
- Per-session apt installs (`iw` in S04, `aircrack-ng` in S05, `hostapd` in S10, `bettercap` in S17, etc.) reinforce the UI-is-teacher / shell-is-proof learning model. The act of consciously installing a tool forces understanding of what it does.
- One real friction point: hostapd-mana (Session 12+) needs build-from-source on Pi OS; Kali ships a prebuilt package. Acceptable trade-off.

**Updated:** `ROADMAP.md`, `README.md`, project memory.

---

## Checkpoint 7 — UI realignment to the actual Hak5 Pineapple

**Why:** Reviewed Hak5's UI docs (`docs.hak5.org/wifi-pineapple/ui-overview/`). My initial nav was tabs across the top with phase-aligned section names — not how the real Pineapple is shaped at all.

**Built:**

- Rewrote `ROADMAP.md` from 17 sessions across 7 phases into **19 sessions across 8 phases**, with each phase mapping 1:1 to a Pineapple top-level section.
- Replaced `base.html` with the Pineapple chrome: fixed top title bar (brand + version + four right-side icon buttons: Notifications bell with dot indicator, Info messages, Web Terminal stub, context menu 3-dots), persistent left sidebar that collapses to 64 px icons and expands to 180 px icons+labels on hover, with all seven Pineapple sections present (Dashboard active, six disabled with tooltips pointing at the session that builds each).
- Rewrote `dashboard.html` to match the real Pineapple's layout: six-card stat row across the top (CPU temp / Memory % / Uptime / Connected Clients / Radios / Reg domain), then a 2-column grid of section cards (Connected Clients / Notifications / Wireless Landscape / Campaigns — all forward-pointing placeholders), then the full Wireless Radios table and Network Interfaces table with real data.
- Rewrote `style.css` for the new chrome — fixed title bar, hover-expand sidebar, Pineapple-style stat cards, two-column dash grid, responsive collapse at 1200 px and 760 px.

**Pineapple patterns to copy** (called out explicitly in the roadmap):

1. **Right-side slide-out detail panel** — click any row, panel slides in from the right with detail and per-target actions. Used in Recon, PineAP, Handshakes. Build once in S06, reuse everywhere.
2. **Allow/Deny filters as first-class** — both clients (by MAC) and SSIDs have independent filter lists, each in Allow or Deny mode. Lands in PineAP phase (S13).
3. **Notifications drawer with severity levels** (Info/Warning/Error/Success/Unknown). Wired in S02 with the WebSocket layer.

**Verified:** All 26 layout checks pass — title bar elements present, sidebar nav items correct with active/disabled state, stat cards row with all six values populating from real data, four placeholder section cards, wireless radios table with three Alfas, "2 in monitor mode" badge.

**Demoted to Modules:** MITM/bettercap and nmap. In the real Pineapple, both would live as Modules (plugin extensions), not as top-level sections. This is actually a cleaner architecture than what I'd originally planned. They'll be built as the first two modules in Sessions 16 and 17, after the module system itself lands in S15.

---

## Checkpoint 8 — Hardware additions: 3 Alfa adapters, Crucial X9 1 TB USB SSD

**Decided:**

- **Three Alfa AWUS036ACM adapters** (up from the original two). Role split with sticky udev names: `wlan-mon-2g` (2.4 GHz monitor), `wlan-mon-5g` (5 GHz monitor or repurposable as injection radio), `wlan-ap` (rogue AP host). Built-in `wlan0` stays on home Wi-Fi for upstream.
- **Three radios unlock:** dual-band simultaneous capture (kills band-steering miss), dual-band Evil Twin, clean concurrent attack+capture. Sessions 04, 06, 07, 11, and 13 specifically take advantage of having three.
- **Crucial X9 1 TB USB SSD** as the boot drive (over the discontinued Samsung T7 500 GB). USB 3.0 caps it at ~400 MB/s but that's ~10× any SD card and more than enough for capture workloads. 250 TBW endurance means it will never wear out at our usage.

**Updated:** `ROADMAP.md`, `README.md`, project memory.

---

## Checkpoint 9 — Pi OS install and SSH access

**Done:**

- Flashed Pi OS Lite Trixie to the Crucial X9 USB SSD via Raspberry Pi Imager.
- Pre-configured in Imager: hostname `pi-lab`, SSH with password auth (deferring SSH-key hardening to Session 19), Wi-Fi country code set (required for regulatory domain even when connecting via Ethernet), locale and timezone set.
- Booted from USB SSD, SSH'd in successfully from the Mac.

**Connection topology:** Ethernet for management (clean separation from offensive Wi-Fi work), `wlan0` reserved for future client-mode upstream (Session 18 settings page).

**Next checkpoint will cover:** apt prerequisites install, project sync from Mac → Pi, venv setup, `python run.py`, first browse from Mac to `http://pi-lab.local:5000`.

---

## Session-wide findings

- The archived `lab-session-01-03.cap` from the pre-pivot session-02 had BSSID `82:4F:94:6E:56:EE` (SSID `"TL"`), not `06:41:A7:58:08:F2` as the old journal recorded. The journal and the actual capture file described different events — the captured handshake was an opportunistic one between the AP and client `14:1B:A0:80:EA:1A`, not the deauthed client `2A:ED:7A:77:62:D2`. **Lesson:** always cross-check journal notes against the actual capture data.
- **Wireshark display filter operator precedence**: `!field == value` parses as `(!field) == value`, which evaluates `!field` as the absence-of-field test (false for every 802.11 frame) and then compares that boolean to a hex literal. Never produces matches. Use `field != value` or wrap in parens: `!(field == value)`. Operator precedence high-to-low: unary `!`, arithmetic, comparison (`==` `!=` `<` `>`), set ops (`contains` `matches` `in`), `&&`, `||`.
- **Pineapple architecture insight:** in the real Pineapple, MITM and nmap are Modules, not top-level sections. The platform's core is recon + rogue AP + handshake capture, and everything else plugs in. This is a cleaner architecture than my initial draft and is what we copied.

---

## Parked questions / TODO

- Set up `udev` sticky-name rules for the three Alfas — deferred to **Session 04** (adapter management).
- Pick a real icon library for the sidebar (currently Unicode glyph placeholders). **Session 02** candidate.
- Investigate `hostapd-mana` build-from-source on Trixie. Will revisit in **Session 12** (Evil WPA tab) — or earlier if a `.deb` shows up that we can use.
- Optional: Mac-side `~/.ssh/config` entry to shorten `ssh nishit@pi-lab.local` to `ssh pi-lab`.
- Optional: eventually migrate from SSH password auth to SSH-key auth. Folded into **Session 19** polish.
