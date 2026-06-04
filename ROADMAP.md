# PiPineapple — Roadmap

A UI-first WiFi pen-testing platform built on the Raspberry Pi 5, modeled directly after the Hak5 WiFi Pineapple MK VII. Each feature is built in a Flask web UI on the Pi; each session ends with a console exercise that runs the same operation by hand, so the UI is never a black box.

## Mission

Two parallel deliverables grow together over the course:

1. **A working Pineapple-style platform** on the Pi — browse to `https://pi-lab.local`, run recon, capture handshakes, crack, run rogue APs, MITM clients, all from the browser.
2. **Deep understanding of every command underneath each button**, gained by dropping to the Pi shell after every feature lands and running the same flow by hand.

The UI is the teacher. The shell is the proof. The Pineapple is the visual reference for what "done" looks like — its docs at `docs.hak5.org/wifi-pineapple/ui-overview/` are the design spec.

## Ground rules

Every attack runs against networks you own or have written authorization to test. Lab uses a dedicated GL.iNet router on its own subnet, lab-only test devices (an old phone, a spare laptop), and the offensive Alfa adapters never touch the home network. Before any session: verify the target MAC is the lab AP, and start each session with the safety checklist in the journal template.

## Hardware

Raspberry Pi 5 (8 GB) running Raspberry Pi OS Lite 64-bit (Bookworm), booting from USB SSD. **Three Alfa AWUS036ACM adapters** on a powered TP-Link UH700 hub. Mac as the operator browser. GL.iNet travel router as the target AP. iPhone + a spare device as victims.

**Adapter role split** with sticky udev names tied to MAC addresses:

- `wlan-mon-2g` — monitor mode on the 2.4 GHz band for recon and capture
- `wlan-mon-5g` — monitor mode on 5 GHz, or repurposed as a dedicated injection/deauth radio
- `wlan-ap` — managed mode, hosts the rogue / Evil Twin AP via hostapd

The Pi's built-in `wlan0` stays on the home network for upstream internet and never participates in offensive operations.

Three radios unlock the three failure modes that bite hardest with two: missed handshakes when the client band-steers, single-band Evil Twins that modern devices ignore, and deauth bursts that corrupt concurrent capture. Sessions 06, 07, 11, and 13 specifically take advantage of having three.

## Tech stack

Python 3.11+ on the Pi (ships by default with Pi OS Bookworm). Flask + Flask-SocketIO for the web app and live updates. Jinja2 templates. Vanilla JS with HTMX where it pays off — no React/Vue/Svelte. SQLite for persistent state. systemd unit to run the service. nginx in front with a self-signed cert in the polish phase.

## OS philosophy

Pi OS Lite is a minimal base. Each session that introduces a new tool starts with an explicit `apt install` for that tool — not a kitchen-sink environment. The act of installing forces you to confront what the tool is for. Session 01 installs the build prerequisites (`python3-pip python3-venv git iw ethtool`); later sessions add their own.

## Modeling the Hak5 Pineapple

PiPineapple's UI mirrors the Hak5 WiFi Pineapple MK VII directly. Same seven top-level sections, same chrome (title bar with notifications/info/web-terminal/context-menu, left sidebar nav), same load-bearing patterns. Cloud C² is the only top-level section we skip (it's Hak5's paid remote-management service).

The seven sections and what they own:

- **Dashboard** — at-a-glance only. Stat cards row, connected-clients table, notifications, campaigns status toggle, wireless-landscape mini-summary.
- **Recon** — universal scanner. AP and client tables with sort/search/pagination. Clicking a row opens a right-side slide-out with tagged parameters (IE-level beacon contents), security info, and per-target actions (capture handshake, deauth all clients, deauth specific client). Handshakes captured in recon flow into the Handshakes section.
- **PineAP** — unified rogue-AP engine. Settings tab with three operation modes (Passive / Active / Advanced) and SSID pool toggles. Open SSID tab. Evil WPA tab (clone a target network, capture partial handshakes). Evil Enterprise tab (WPA-EAP rogue with cert generation, MSCHAPv2/GTC/Any). Impersonation tab (SSID pool, manual + auto). Clients tab (connected + previous, kick). Filtering tab (Client and SSID Allow/Deny lists). Access Points tab.
- **Handshakes** — top-level page listing all captured handshakes from any source, labeled by origin (Recon Capture / Evil WPA / Evil Enterprise). Download as pcap and Hashcat 22000. Crack action dispatches off-Pi.
- **Campaigns** — scripted attack templates: Reconnaissance (Monitor Only), Client Device Assessment (Passive), Client Device Assessment (Active). Per-campaign enable/disable toggle. Reports subtab — JSON or HTML, downloadable.
- **Modules** — plugin system. Community-style extensions to the UI. MITM (bettercap) and nmap live here as modules rather than top-level sections. Packages subtab for CLI-only tools.
- **Settings** — Networking (recon interface selector, client mode upstream, USB ethernet), WiFi (management network), Advanced (Censorship Mode for redacting MACs/SSIDs in screenshots, hostname, management access scoping), Help/Diagnostics.
- **Learning Centre** *(project-specific addition, not in the Pineapple)* — curriculum-as-feature page accreting console commands by topic. Every session adds either a new topic section or new commands to existing sections, so the platform documents how to use it as it grows.

**Three load-bearing UI patterns** that show up across multiple sections and need to be built right:

1. **Right-side slide-out detail panel.** Click any row → panel slides in from the right with full detail and actions. Used for APs in Recon, for clients in PineAP, for handshakes. Reduces page-hopping enormously.
2. **Allow/Deny filters as first-class.** Both clients (MAC) and SSIDs have independent filter lists, each operating in Allow mode (only these can connect) or Deny mode (everyone except these). Without this layer, the rogue AP either grabs everyone (legal/ethical problem) or nobody (filter set wrong).
3. **Notifications drawer with severity levels.** Info / Warning / Error / Success / Unknown. Modules and the system both emit notifications, briefly previewed in the title bar then archived to the drawer.

## Project layout

```
pipineapple/
  app/
    __init__.py            # Flask app factory
    config.py              # Dev / Test / Prod configs
    routes/                # One blueprint per section
      dashboard.py
      recon.py             # added in Phase B
      pineap.py            # added in Phase D
      handshakes.py        # added in Phase C
      campaigns.py         # added in Phase F
      modules.py           # added in Phase G
      settings.py          # added in Phase H
    services/              # Stateful orchestration (sysinfo, JobManager, parsers)
    tools/                 # Subprocess wrappers (airodump, hostapd, ...)
    templates/
      base.html            # title bar + sidebar chrome
      <section>/*.html     # per-section templates
    static/
  sessions/                # Per-session journals
  tests/
  pyproject.toml
  run.py
  README.md
  ROADMAP.md               # this file
```

Strict dependency direction: `routes/` → `services/` → `tools/`. Routes are thin; services orchestrate; tools encapsulate subprocesses.

## Curriculum

Eight phases, nineteen sessions. Each session lands one Pineapple-section feature in the UI plus its console exercise.

### Phase A — Dashboard & chrome

**Session 01.** *Done.* Project scaffold, Flask app factory, base template, **system status data layer** (CPU temp, memory, IPs, adapter list with mode/channel/driver). Pineapple chrome (title bar + hover-expand left sidebar) shipped early as part of the mid-session IA realignment. Dashboard layout matching the Pineapple's (stat cards row + four placeholder section cards + wireless radios + interfaces tables). Learning Centre section established. Console exercise covered `iw dev`, `ip -j addr show`, `/proc/net/wireless`, `vcgencmd measure_temp`, `iw reg get`, `ethtool -i`.

**Session 02.** **Realtime layer + Notifications + JobManager.** WebSocket integration via Flask-SocketIO so the dashboard's stat cards and tables update live without a page refresh. Notifications system fully wired — the bell icon's dot indicator turns on for unread, dropdown drawer lists last N messages with the five Pineapple severity levels (Info / Warning / Error / Success / Unknown), backend can `emit("notification", ...)` from anywhere. JobManager service skeleton — owns subprocess lifecycles for every later session's long-running tools (airodump, hostapd, hashcat, etc.), streams stdout to subscribed UI clients, handles signal-based teardown. Replace Unicode-glyph icons with real inline SVG. Console: subprocess lifecycle, signal handling, eventlet vs threading async modes for SocketIO.

**Session 03.** *Folded into Session 01.* Originally planned as a separate "full dashboard layout" session, but the IA realignment in S01 delivered this scope already. Sessions renumber from here — what was S04 is now S03, etc. (Phase B starts at S03.)

### Phase B — Recon

**Session 04.** **Adapter management** — Settings page early start. Sticky udev names for the three Alfa adapters, monitor mode toggle per adapter from the UI, "current Recon interface" selector. Backend wraps `airmon-ng start/stop` and `iw dev <iface> set type monitor/managed`. Console: NetworkManager unmanaging via `NetworkManager.conf`, why `airmon-ng check kill` matters.

**Session 05.** **Recon scan table** — start/stop airodump in background on the selected recon interface, parse CSV rotation, push AP and client lists to UI via WebSocket. Sortable columns, searchable by BSSID/SSID/MAC, paginated. With three adapters, two parallel scans (`wlan-mon-2g` band-locked to 2.4 GHz, `wlan-mon-5g` to 5 GHz) merged into one view. Console: airodump's `-w` CSV format, channel hopping vs band-locked.

**Session 06.** **Recon slide-out detail panel** — click an AP row → right-side slide-out with: tagged parameters viewer (parsed IEs from the beacon), security info panel (RSN cipher suites, key management), per-target action menu (Capture Handshakes / Deauth All Clients / Deauth Specific Client / Add to PineAP Filter). Backend deauth dispatched on the injection radio (third Alfa) to keep capture radios clean. Console: deauth frame structure, the MFP-protected exception, the slide-out as a UX pattern.

### Phase C — Handshakes

**Session 07.** **Direct handshake capture** triggered from the Recon slide-out. Backend locks airodump to the target's channel/BSSID, optionally deauths concurrently to force reconnection, watches for EAPOL frames, declares success when M1-M4 are seen. With three adapters, dual-band capture for dual-band APs. Console: `-c`, `--bssid`, the EAPOL 4-way structure, the band-steering miss mode that three radios solve.

**Session 08.** **Handshakes top-level page** — list every captured handshake with source label (Recon Capture / Evil WPA / Evil Enterprise — last two arrive in Phase D), AP SSID/BSSID, client MAC, capture timestamp, complete-vs-partial status. Download as pcap and `.hc22000`. Automatic-collection toggle that pulls EAPOL frames from any active recon scan. Console: `hcxpcapngtool -o out.hc22000 in.cap`, the .hc22000 format, partial-handshake usability.

**Session 09.** **Crack action** from the Handshakes page. Backend dispatches off-Pi (`scp` + remote `hashcat -m 22000` over SSH to Mac or Jetson — chosen per-job in the UI). Optionally local `aircrack-ng` fallback for small wordlists. Live progress streaming back to the UI via the JobManager. Console: hashcat mode 22000 internals, rule files, the Pi-vs-Mac-vs-Jetson hardware decision (revisits the segfault writeup from archived material).

### Phase D — PineAP

**Session 10.** **PineAP Settings tab** — Passive / Active / Advanced operation modes, SSID pool capture toggle (auto-add SSIDs from observed probe requests and recon scan results), broadcast SSID pool toggle (advertise the collected pool as fake beacons). Backend wraps `hostapd` config generation. Console: hostapd.conf field-by-field.

**Session 11.** **Open SSID tab** — basic open AP, visible/hidden toggle, Impersonate-All-Networks mode. Backend bring-up sequence: static IP on `wlan-ap`, dnsmasq for DHCP+DNS, hostapd in `auth_algs=1`. With three adapters, dual-band Open SSID becomes practical. Console: dnsmasq.conf, captive-portal probe traffic from iOS/Android (`captive.apple.com`, `connectivitycheck.gstatic.com`).

**Session 12.** **Evil WPA tab** — clone an existing WPA target (one click from Recon's slide-out via "Clone to PineAP"), or build a new WPA SSID from scratch. Backend captures partial handshakes from clients that attempt to associate and feeds them to the Handshakes section. Console: how partial handshakes are still crackable, what makes a clone convincing.

**Session 13.** **PineAP Impersonation, Filtering, Clients tabs** — SSID Impersonation Pool (manual + auto-collect from probes/recon, BSSID strategy: single vs pseudo-random per SSID), Allow/Deny filters on both clients (by MAC) and SSIDs, Clients view with connected + previous + kick action. The filter UI is where you learn why this layer exists. Console: how filters translate to hostapd's `accept_mac_file` / `deny_mac_file`.

### Phase E — Campaigns

**Session 14.** **Campaigns page** — three campaign templates: Reconnaissance (Monitor Only), Client Device Assessment (Passive), Client Device Assessment (Active). Each is a backed-by-script template that orchestrates recon + PineAP modes + handshake collection over a defined window. Reports subtab generates JSON and HTML reports of what was captured. Console: the shell script template under the hood, why "campaign" is the abstraction that makes the platform usable for real engagements.

### Phase F — Modules

**Session 15.** **Modules system architecture** — drop-in Python plugin loader. A module is a directory under `app/modules/<name>/` with a manifest (`module.toml`), routes, templates, and tools. The Modules page lists installed modules and exposes install/uninstall (from a local repo dir). Sidebar registration is automatic. Console: how the plugin loader walks the directory and registers blueprints dynamically.

**Session 16.** **nmap module** — post-association recon module. UI to run `nmap` against the Open AP's connected clients (PineAP Clients) or against the lab subnet after a PSK crack. Subnet discovery (`-sn`), service detect (`-sV`), targeted scans, NSE bundles rendered as sortable tables. Console: nmap modes, NSE script categories, scanning from your gateway IP vs being elsewhere.

**Session 17.** **MITM module** — bettercap integration. UI panel for ARP spoof + DNS spoof against selected PineAP clients. Backend launches bettercap with a generated caplet, parses `events.stream` JSON output. Live traffic inspection: DNS queries, HTTP hosts, captured creds. Captive portal builder — drop a template HTML file served by the Pi for DNS-spoofed hostnames. Console: bettercap caplets, why most modern traffic resists this (HSTS, cert pinning), what the periphery (captive portals, OS connectivity checks) still leaks.

### Phase G — Settings & polish

**Session 18.** **Settings page** — Networking (client mode for `wlan0` upstream, USB ethernet, route table), WiFi (management network on `wlan0`), Advanced (Censorship Mode that redacts MAC/SSID/BSSID in the UI for screenshots and streams, hostname change, management access scoping). Console: the censorship mode as a real concept for sharing pen-test screenshots safely.

**Session 19.** **Production polish** — Authentication (login screen, simple session-based auth, configurable password from Settings), HTTPS via self-signed cert generated on first run, systemd unit (`pipineapple.service`) running the app via gunicorn behind nginx, real Web Terminal wiring (xterm.js front-end + a backend wrapping a constrained bash session). Deployment runbook. Console: nginx config, systemd unit, the security trade-offs of hosting a web shell.

## Per-session shape

Every session follows the same pattern:

1. **Outline the feature** — what the button does and why it's the next logical thing.
2. **Build the UI piece** — Flask route + Jinja template + minimal JS.
3. **Wire the backend** — subprocess wrapper for the underlying tool.
4. **Run it in the browser** — from the Mac, against the lab.
5. **Drop to the Pi shell** — run the same operation by hand to understand the wrapper.
6. **Journal** — `sessions/session-NN.md` with what was built, what the console exercise revealed, parked questions.

## Cadence

Reasonable evening pace: one session per evening, occasionally splitting a heavier session across two. Full course is 7–9 weeks. Don't rush Phase A — the chrome and sidebar set the visual standard everything else lives inside.

## Archived

The pre-pivot console-first roadmap, cheatsheet, and sessions 01–02 are at `../archive/` for reference. The nmap notes, the hashcat-on-Pi-5 segfault writeup, and the EAPOL deep dive in archived session-02 remain useful background reading.
